# -*- coding: utf-8 -*-
"""Security-focused tests for common URL utility helpers."""
import base64
import socket
from unittest import TestCase
from unittest.mock import Mock, patch

from agentscope._utils._common import _get_bytes_from_web_url


class CommonUtilsSecurityTest(TestCase):
    """Test URL fetch hardening against SSRF and unsafe redirects."""

    @patch("agentscope._utils._common.requests.get")
    def test_reject_literal_loopback_ip(self, mock_get: Mock) -> None:
        """Loopback IP URLs should be blocked before request."""
        with self.assertRaises(RuntimeError):
            _get_bytes_from_web_url(
                "http://127.0.0.1/internal",
                max_retries=1,
            )
        mock_get.assert_not_called()

    @patch("agentscope._utils._common.requests.get")
    @patch("agentscope._utils._common.socket.getaddrinfo")
    def test_reject_private_ip_resolution(
        self,
        mock_getaddrinfo: Mock,
        mock_get: Mock,
    ) -> None:
        """Hostnames resolving to private IP addresses should be blocked."""
        mock_getaddrinfo.return_value = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("10.0.0.2", 0),
            ),
        ]
        with self.assertRaises(RuntimeError):
            _get_bytes_from_web_url(
                "http://example.internal/resource",
                max_retries=1,
            )
        mock_get.assert_not_called()

    @patch("agentscope._utils._common.requests.get")
    @patch("agentscope._utils._common.socket.getaddrinfo")
    def test_allow_public_ip_resolution(
        self,
        mock_getaddrinfo: Mock,
        mock_get: Mock,
    ) -> None:
        """Publicly routable hostnames should be fetched successfully."""
        mock_getaddrinfo.return_value = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 0),
            ),
        ]
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"hello"
        mock_response.headers = {}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = _get_bytes_from_web_url(
            "http://example.com/resource",
            max_retries=1,
        )
        self.assertEqual(result, "hello")
        mock_get.assert_called_once_with(
            "http://example.com/resource",
            allow_redirects=False,
            timeout=(5, 10),
        )

    @patch("agentscope._utils._common.requests.get")
    @patch("agentscope._utils._common.socket.getaddrinfo")
    def test_block_redirect_to_loopback(
        self,
        mock_getaddrinfo: Mock,
        mock_get: Mock,
    ) -> None:
        """Redirect targets should be validated before follow-up requests."""
        mock_getaddrinfo.return_value = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 0),
            ),
        ]
        mock_response = Mock()
        mock_response.status_code = 302
        mock_response.headers = {"Location": "http://127.0.0.1/internal"}
        mock_get.return_value = mock_response

        with self.assertRaises(RuntimeError):
            _get_bytes_from_web_url(
                "http://example.com/redirect",
                max_retries=1,
            )
        self.assertEqual(mock_get.call_count, 1)

    @patch("agentscope._utils._common.requests.get")
    @patch("agentscope._utils._common.socket.getaddrinfo")
    def test_binary_content_falls_back_to_base64(
        self,
        mock_getaddrinfo: Mock,
        mock_get: Mock,
    ) -> None:
        """Non-UTF8 payloads should return base64-encoded content."""
        mock_getaddrinfo.return_value = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 0),
            ),
        ]
        payload = b"\xff\x00"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = payload
        mock_response.headers = {}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = _get_bytes_from_web_url(
            "http://example.com/binary",
            max_retries=1,
        )
        self.assertEqual(result, base64.b64encode(payload).decode("ascii"))
