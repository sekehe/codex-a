#!/usr/bin/env python3
"""验证 URL 有效性的脚本。

用法示例：
  python validate_url.py https://www.openai.com
  python validate_url.py https://a.com https://b.com --timeout 5
  python validate_url.py --file urls.txt --strict
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from dataclasses import dataclass
from typing import Iterable, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass
class ValidationResult:
    url: str
    is_valid: bool
    reason: str
    status_code: int | None = None
    retries: int = 0


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if "://" not in url:
        # 默认补全为 https
        return f"https://{url}"
    return url


def check_structure(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, "仅支持 http/https 协议"
    if not parsed.netloc:
        return False, "缺少主机名"
    return True, "结构合法"


def check_dns(hostname: str) -> tuple[bool, str]:
    try:
        socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False, "DNS 解析失败"
    return True, "DNS 可解析"


def _compute_wait_seconds(retry_after: str | None, base_delay: float, attempt: int) -> float:
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            pass
    # 指数退避 + 抖动，避免短时间重复打到同一服务
    jitter = random.uniform(0, base_delay)
    return base_delay * (2 ** attempt) + jitter


def check_reachable(
    url: str,
    timeout: float,
    max_retries: int,
    base_delay: float,
    user_agent: str,
) -> tuple[bool, str, int | None, int]:
    # 先尝试 HEAD，若服务不支持则回退 GET
    retries_done = 0
    for method in ("HEAD", "GET"):
        for attempt in range(max_retries + 1):
            req = Request(url=url, method=method, headers={"User-Agent": user_agent})
            try:
                with urlopen(req, timeout=timeout) as resp:
                    status = getattr(resp, "status", None)
                    if status is None:
                        return True, f"{method} 请求成功", None, retries_done
                    if 200 <= status < 400:
                        return True, f"{method} 请求成功", status, retries_done
                    return False, f"HTTP 状态码异常: {status}", status, retries_done
            except HTTPError as e:
                if e.code == 429 and attempt < max_retries:
                    retries_done += 1
                    wait_seconds = _compute_wait_seconds(
                        e.headers.get("Retry-After"),
                        base_delay=base_delay,
                        attempt=attempt,
                    )
                    time.sleep(wait_seconds)
                    continue
                return False, f"HTTP 错误: {e.code}", e.code, retries_done
            except URLError as e:
                # 某些服务拒绝 HEAD，继续尝试 GET
                if method == "HEAD":
                    break
                if attempt < max_retries:
                    retries_done += 1
                    wait_seconds = _compute_wait_seconds(None, base_delay=base_delay, attempt=attempt)
                    time.sleep(wait_seconds)
                    continue
                return False, f"网络错误: {e.reason}", None, retries_done
            except TimeoutError:
                if attempt < max_retries:
                    retries_done += 1
                    wait_seconds = _compute_wait_seconds(None, base_delay=base_delay, attempt=attempt)
                    time.sleep(wait_seconds)
                    continue
                return False, "请求超时", None, retries_done
    return False, "无法访问", None, retries_done


def check_reachable_with_browser(
    url: str,
    timeout: float,
    max_retries: int,
    base_delay: float,
    user_agent: str,
) -> tuple[bool, str, int | None, int]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception:
        return False, "未安装 playwright，无法使用无头浏览器模式", None, 0

    retries_done = 0
    timeout_ms = int(max(timeout, 0.1) * 1000)

    for attempt in range(max_retries + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=user_agent)
                page = context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                status = response.status if response is not None else None
                context.close()
                browser.close()

            if status is None:
                return True, "浏览器访问成功（无响应状态码）", None, retries_done
            if 200 <= status < 400:
                return True, "浏览器访问成功", status, retries_done
            if status == 429 and attempt < max_retries:
                retries_done += 1
                time.sleep(_compute_wait_seconds(None, base_delay=base_delay, attempt=attempt))
                continue
            return False, f"浏览器访问返回状态码: {status}", status, retries_done
        except PlaywrightTimeoutError:
            if attempt < max_retries:
                retries_done += 1
                time.sleep(_compute_wait_seconds(None, base_delay=base_delay, attempt=attempt))
                continue
            return False, "浏览器访问超时", None, retries_done
        except Exception as e:
            if attempt < max_retries:
                retries_done += 1
                time.sleep(_compute_wait_seconds(None, base_delay=base_delay, attempt=attempt))
                continue
            return False, f"浏览器访问失败: {e}", None, retries_done

    return False, "浏览器访问失败", None, retries_done


def validate_url(
    url: str,
    timeout: float,
    strict: bool,
    max_retries: int,
    base_delay: float,
    user_agent: str,
    engine: str,
) -> ValidationResult:
    raw_url = url
    url = normalize_url(url)

    ok, reason = check_structure(url)
    if not ok:
        return ValidationResult(raw_url, False, reason, retries=0)

    parsed = urlparse(url)
    assert parsed.hostname is not None

    ok, reason = check_dns(parsed.hostname)
    if not ok:
        return ValidationResult(raw_url, False, reason, retries=0)

    if strict:
        if engine == "browser":
            ok, reason, status_code, retries = check_reachable_with_browser(
                url=url,
                timeout=timeout,
                max_retries=max_retries,
                base_delay=base_delay,
                user_agent=user_agent,
            )
        else:
            ok, reason, status_code, retries = check_reachable(
                url=url,
                timeout=timeout,
                max_retries=max_retries,
                base_delay=base_delay,
                user_agent=user_agent,
            )
        return ValidationResult(raw_url, ok, reason, status_code, retries)

    return ValidationResult(raw_url, True, "格式和 DNS 校验通过", retries=0)


def read_urls(args: argparse.Namespace) -> List[str]:
    urls: List[str] = []
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    urls.extend(args.urls)
    return urls


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 URL 的有效性")
    parser.add_argument("urls", nargs="*", help="待验证的 URL（可省略协议）")
    parser.add_argument("--file", "-f", help="从文件批量读取 URL（每行一个）")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="启用严格模式：额外发起网络请求验证 URL 可访问性",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="网络请求超时时间（秒）")
    parser.add_argument("--retries", type=int, default=2, help="严格模式下的最大重试次数")
    parser.add_argument(
        "--base-delay",
        type=float,
        default=1.0,
        help="严格模式重试基础等待时间（秒），实际等待将指数退避并加入随机抖动",
    )
    parser.add_argument(
        "--user-agent",
        default="url-validator/1.0 (+https://example.local)",
        help="严格模式请求使用的 User-Agent（建议使用固定且可识别的值，避免随机指纹）",
    )
    parser.add_argument(
        "--engine",
        choices=("http", "browser"),
        default="http",
        help="严格模式校验引擎：http(默认) 或 browser(无头浏览器，需要 playwright)",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    urls = read_urls(args)

    if not urls:
        print("未提供任何 URL。可通过参数或 --file 输入。", file=sys.stderr)
        return 2

    has_invalid = False
    for url in urls:
        result = validate_url(
            url=url,
            timeout=args.timeout,
            strict=args.strict,
            max_retries=max(0, args.retries),
            base_delay=max(0.1, args.base_delay),
            user_agent=args.user_agent,
            engine=args.engine,
        )
        prefix = "✅" if result.is_valid else "❌"
        extra = f" (status={result.status_code})" if result.status_code is not None else ""
        retry_msg = f" [retries={result.retries}]" if result.retries else ""
        print(f"{prefix} {result.url} -> {result.reason}{extra}{retry_msg}")
        has_invalid = has_invalid or (not result.is_valid)

    return 1 if has_invalid else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
