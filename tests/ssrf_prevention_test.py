# -*- coding: utf-8 -*-
"""Tests for SSRF prevention in _get_bytes_from_web_url."""

import pytest

from agentscope._utils._common import _is_private_url


class TestIsPrivateUrl:
    """Tests for _is_private_url SSRF prevention."""

    def test_public_url_is_safe(self) -> None:
        """Public URLs should be allowed."""
        assert _is_private_url("https://example.com/image.png") is False

    def test_localhost_is_blocked(self) -> None:
        """localhost should be blocked."""
        assert _is_private_url("http://localhost:8080/") is True

    def test_127_0_0_1_is_blocked(self) -> None:
        """127.0.0.1 should be blocked."""
        assert _is_private_url("http://127.0.0.1/") is True

    def test_10_0_0_1_is_blocked(self) -> None:
        """10.0.0.1 (private subnet) should be blocked."""
        assert _is_private_url("http://10.0.0.1/") is True

    def test_192_168_1_1_is_blocked(self) -> None:
        """192.168.1.1 (private subnet) should be blocked."""
        assert _is_private_url("http://192.168.1.1/") is True

    def test_172_16_0_1_is_blocked(self) -> None:
        """172.16.0.1 (private subnet) should be blocked."""
        assert _is_private_url("http://172.16.0.1/") is True

    def test_link_local_is_blocked(self) -> None:
        """169.254.x.x (link-local) should be blocked."""
        assert _is_private_url("http://169.254.1.1/") is True

    def test_file_protocol_is_blocked(self) -> None:
        """file:// protocol should be blocked."""
        assert _is_private_url("file:///etc/passwd") is True

    def test_no_hostname_is_blocked(self) -> None:
        """URLs without a hostname should be blocked."""
        assert _is_private_url("http:///path") is True
