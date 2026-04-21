#!/usr/bin/env python3
"""验证 URL 有效性的脚本。

用法示例：
  python validate_url.py https://www.openai.com
  python validate_url.py https://a.com https://b.com --timeout 5
  python validate_url.py --file urls.txt --strict
"""

from __future__ import annotations

import argparse
import socket
import sys
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


def check_reachable(url: str, timeout: float) -> tuple[bool, str, int | None]:
    # 先尝试 HEAD，若服务不支持则回退 GET
    for method in ("HEAD", "GET"):
        req = Request(url=url, method=method, headers={"User-Agent": "url-validator/1.0"})
        try:
            with urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None)
                if status is None:
                    return True, f"{method} 请求成功", None
                if 200 <= status < 400:
                    return True, f"{method} 请求成功", status
                return False, f"HTTP 状态码异常: {status}", status
        except HTTPError as e:
            # 4xx/5xx 说明服务器可达，但URL可能不可用
            return False, f"HTTP 错误: {e.code}", e.code
        except URLError as e:
            # 某些服务拒绝 HEAD，继续尝试 GET
            if method == "HEAD":
                continue
            return False, f"网络错误: {e.reason}", None
        except TimeoutError:
            return False, "请求超时", None
    return False, "无法访问", None


def validate_url(url: str, timeout: float, strict: bool) -> ValidationResult:
    raw_url = url
    url = normalize_url(url)

    ok, reason = check_structure(url)
    if not ok:
        return ValidationResult(raw_url, False, reason)

    parsed = urlparse(url)
    assert parsed.hostname is not None

    ok, reason = check_dns(parsed.hostname)
    if not ok:
        return ValidationResult(raw_url, False, reason)

    if strict:
        ok, reason, status_code = check_reachable(url, timeout)
        return ValidationResult(raw_url, ok, reason, status_code)

    return ValidationResult(raw_url, True, "格式和 DNS 校验通过")


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
    return parser.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    urls = read_urls(args)

    if not urls:
        print("未提供任何 URL。可通过参数或 --file 输入。", file=sys.stderr)
        return 2

    has_invalid = False
    for url in urls:
        result = validate_url(url=url, timeout=args.timeout, strict=args.strict)
        prefix = "✅" if result.is_valid else "❌"
        extra = f" (status={result.status_code})" if result.status_code is not None else ""
        print(f"{prefix} {result.url} -> {result.reason}{extra}")
        has_invalid = has_invalid or (not result.is_valid)

    return 1 if has_invalid else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
