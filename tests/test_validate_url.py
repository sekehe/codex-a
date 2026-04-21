import unittest
from unittest.mock import patch

import validate_url


class TestValidateURL(unittest.TestCase):
    def test_normalize_url_adds_https(self):
        self.assertEqual(validate_url.normalize_url("example.com"), "https://example.com")

    def test_check_structure_invalid_scheme(self):
        ok, reason = validate_url.check_structure("ftp://example.com")
        self.assertFalse(ok)
        self.assertIn("http/https", reason)

    @patch("validate_url.socket.getaddrinfo")
    def test_check_dns_ok(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, None)]
        ok, reason = validate_url.check_dns("example.com")
        self.assertTrue(ok)
        self.assertIn("DNS 可解析", reason)

    @patch("validate_url.check_dns")
    def test_validate_url_non_strict_success(self, mock_check_dns):
        mock_check_dns.return_value = (True, "DNS 可解析")
        result = validate_url.validate_url(
            url="example.com",
            timeout=1.0,
            strict=False,
            max_retries=0,
            base_delay=0.1,
            user_agent="test-agent",
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.reason, "格式和 DNS 校验通过")

    @patch("validate_url.check_dns")
    @patch("validate_url.check_reachable")
    def test_validate_url_strict_uses_playwright_checker(self, mock_check_reachable, mock_check_dns):
        mock_check_dns.return_value = (True, "DNS 可解析")
        mock_check_reachable.return_value = (True, "浏览器访问成功", 200, 1)

        result = validate_url.validate_url(
            url="https://example.com",
            timeout=1.0,
            strict=True,
            max_retries=1,
            base_delay=0.1,
            user_agent="test-agent",
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.retries, 1)

    def test_looks_abnormal_page(self):
        self.assertTrue(validate_url._looks_abnormal_page("Please complete CAPTCHA", "Access Denied"))
        self.assertFalse(validate_url._looks_abnormal_page("Welcome to homepage", "Home"))


if __name__ == "__main__":
    unittest.main()
