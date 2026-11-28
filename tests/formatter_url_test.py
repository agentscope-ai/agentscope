# -*- coding: utf-8 -*-
"""Regression tests for untrusted formatter media URLs."""
import socket
import ssl
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

import httpx

from agentscope.formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
    GeminiChatFormatter,
    GeminiMultiAgentFormatter,
    MoonshotChatFormatter,
    MoonshotMultiAgentFormatter,
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)
from agentscope.formatter._download import _download_media
from agentscope.message import DataBlock, URLSource, UserMsg


def _addresses(*addresses: str) -> list[tuple]:
    """Build deterministic TCP resolver results for the given addresses."""
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, 443),
        )
        for address in addresses
    ]


class MediaDownloadTest(TestCase):
    """Exercise URL policy through HTTPX without external network access."""

    def setUp(self) -> None:
        """Replace resolution and HTTP transport, leaving URL handling real."""
        dns = patch(
            "agentscope.formatter._download.socket.getaddrinfo",
            return_value=_addresses("93.184.216.34"),
        )
        self.dns = dns.start()
        self.addCleanup(dns.stop)
        send = patch(
            "httpx.HTTPTransport.handle_request",
            autospec=True,
            return_value=httpx.Response(200, content=b"media"),
        )
        self.send = send.start()
        self.addCleanup(send.stop)

    def test_nonpublic_addresses_are_rejected(self) -> None:
        """Reject local, special-use and IPv6 transition destinations."""
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.0.1",
            "169.254.169.254",
            "100.100.100.200",
            "0.0.0.0",
            "192.0.0.8",
            "192.0.2.1",
            "198.18.0.1",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
            "::",
            "::1",
            "fe80::1",
            "fc00::1",
            "ff02::1",
            "2001:db8::1",
            "::ffff:127.0.0.1",
            "64:ff9b::7f00:1",
            "2002:7f00:1::",
            "2001::1",
        ):
            with self.subTest(address=address):
                self.dns.return_value = _addresses(address)
                with self.assertRaisesRegex(ValueError, "public IP"):
                    _download_media("https://media.example/file")
        self.send.assert_not_called()

    def test_mixed_dns_answers_are_rejected(self) -> None:
        """A public first result cannot hide a private later answer."""
        for addresses in (
            ("93.184.216.34", "127.0.0.1"),
            ("93.184.216.34", "::1"),
            ("fc00::1", "93.184.216.34"),
        ):
            with self.subTest(addresses=addresses):
                self.dns.return_value = _addresses(*addresses)
                with self.assertRaisesRegex(ValueError, "public IP"):
                    _download_media("https://media.example/file")
        self.send.assert_not_called()

    def test_invalid_urls_are_rejected_before_dns(self) -> None:
        """Only absolute HTTP(S) URLs without credentials are accepted."""
        for url in (
            "file:///etc/passwd",
            "ftp://media.example/file",
            "gopher://media.example/file",
            "//media.example/file",
            "https:///file",
            "https://user:secret@media.example/file",
            "https://user@media.example/file",
            "http://[fe80::1%25eth0]/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    _download_media(url)
        self.dns.assert_not_called()
        self.send.assert_not_called()

    def test_resolution_failure_is_closed(self) -> None:
        """No request is sent for empty or failed DNS resolution."""
        self.dns.return_value = []
        with self.assertRaisesRegex(ValueError, "public IP"):
            _download_media("https://media.example/file")
        self.dns.side_effect = socket.gaierror("resolution failed")
        with self.assertRaises(socket.gaierror):
            _download_media("https://media.example/file")
        self.send.assert_not_called()

    def test_public_download_preserves_host_path_and_tls_name(self) -> None:
        """Connect to the checked IP while routing to the original host."""
        with patch("httpx.Client", wraps=httpx.Client) as client:
            result = _download_media(
                "https://media.example:8443/a%20b.pdf?signature=abc#ignored",
            )
        self.assertEqual(result, b"media")
        client.assert_called_once_with(timeout=30, trust_env=False)
        request = self.send.call_args.args[1]
        self.assertEqual(
            (str(request.url), request.headers["host"], request.extensions),
            (
                "https://93.184.216.34:8443/a%20b.pdf?signature=abc#ignored",
                "media.example:8443",
                {
                    "sni_hostname": "media.example",
                    "timeout": {
                        "connect": 30,
                        "read": 30,
                        "write": 30,
                        "pool": 30,
                    },
                },
            ),
        )

    def test_public_ipv6_and_idna_hosts(self) -> None:
        """IPv6 targets and international hostnames retain valid routing."""
        for url, address, host in (
            (
                "http://[2606:4700:4700::1111]:8080/a",
                "2606:4700:4700::1111",
                "[2606:4700:4700::1111]:8080",
            ),
            (
                "https://bücher.example/a",
                "93.184.216.34",
                "xn--bcher-kva.example",
            ),
        ):
            with self.subTest(url=url):
                self.dns.return_value = _addresses(address)
                self.send.return_value = httpx.Response(200, content=b"media")
                self.assertEqual(_download_media(url), b"media")
                request = self.send.call_args.args[1]
                self.assertEqual(
                    (request.url.host, request.headers["host"]),
                    (address, host),
                )

    def test_redirect_to_private_address_is_rejected(self) -> None:
        """Re-resolve same-host redirects before making another request."""
        for location in ("http://127.0.0.1/private", "/private"):
            with self.subTest(location=location):
                self.dns.side_effect = [
                    _addresses("93.184.216.34"),
                    _addresses("127.0.0.1"),
                ]
                self.send.reset_mock()
                self.send.return_value = httpx.Response(
                    302,
                    headers={"Location": location},
                )
                with self.assertRaisesRegex(ValueError, "public IP"):
                    _download_media("https://media.example/file")
                self.assertEqual(self.send.call_count, 1)

    def test_public_redirects_are_resolved_again(self) -> None:
        """Follow relative and cross-host redirects without sharing cookies."""
        self.send.side_effect = [
            httpx.Response(302, headers={"Location": "/next?token=abc"}),
            httpx.Response(
                307,
                headers={
                    "Location": "https://cdn.example/final",
                    "Set-Cookie": "private=value; Path=/",
                },
            ),
            httpx.Response(200, content=b"final"),
        ]
        self.assertEqual(
            _download_media("https://media.example/file"),
            b"final",
        )
        self.assertEqual(
            [
                (
                    request.url.raw_path,
                    request.headers["host"],
                    request.extensions["sni_hostname"],
                    request.headers.get("cookie"),
                )
                for _, request in (
                    item.args for item in self.send.call_args_list
                )
            ],
            [
                (b"/file", "media.example", "media.example", None),
                (b"/next?token=abc", "media.example", "media.example", None),
                (b"/final", "cdn.example", "cdn.example", None),
            ],
        )
        self.assertEqual(self.dns.call_count, 3)

    def test_redirect_limit(self) -> None:
        """A redirect loop stops without unbounded requests."""
        self.send.side_effect = lambda *_: httpx.Response(
            302,
            headers={"Location": "/again"},
        )
        with self.assertRaisesRegex(ValueError, "5 redirects"):
            _download_media("https://media.example/file")
        self.assertEqual(self.send.call_count, 6)

    def test_redirect_cannot_change_scheme_or_add_credentials(self) -> None:
        """Redirect targets must pass the same URL validation as input."""
        for location in (
            "file:///etc/passwd",
            "ftp://media.example/file",
            "https://user:secret@media.example/file",
        ):
            with self.subTest(location=location):
                self.send.reset_mock()
                self.send.return_value = httpx.Response(
                    302,
                    headers={"Location": location},
                )
                with self.assertRaises(ValueError):
                    _download_media("https://media.example/file")
                self.assertEqual(self.send.call_count, 1)

    def test_public_address_fallback_and_errors(self) -> None:
        """Retry another checked address only for connection failures."""
        self.dns.return_value = _addresses(
            "2606:4700:4700::1111",
            "93.184.216.34",
        )
        self.send.side_effect = [
            httpx.ConnectTimeout("unreachable"),
            httpx.Response(200, content=b"media"),
        ]
        self.assertEqual(
            _download_media("https://media.example/file"),
            b"media",
        )
        for error in (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
        ):
            with self.subTest(error=error):
                self.send.reset_mock()
                self.send.side_effect = error("failed")
                with self.assertRaises(error):
                    _download_media("https://media.example/file")
                self.assertEqual(
                    self.send.call_count,
                    1 if error is httpx.ReadTimeout else 2,
                )

    def test_http_failure_is_not_encoded_as_media(self) -> None:
        """Reject unsuccessful responses instead of forwarding error bodies."""
        for status in (301, 400, 404, 500):
            with self.subTest(status=status):
                self.send.return_value = httpx.Response(status)
                with self.assertRaises(httpx.HTTPStatusError):
                    _download_media("https://media.example/file")


class MediaConnectionTest(TestCase):
    """Verify pinning and TLS through the real HTTPX transport."""

    def test_obfuscated_loopback_urls(self) -> None:
        """Resolve numeric aliases before deciding whether they are public."""
        with patch("httpx.HTTPTransport.handle_request") as send:
            for host in (
                "127.1",
                "2130706433",
                "0x7f000001",
                "[::ffff:127.0.0.1]",
            ):
                with self.subTest(host=host):
                    with self.assertRaises((ValueError, socket.gaierror)):
                        _download_media(f"http://{host}/file")
            send.assert_not_called()

    def test_connection_uses_checked_ip_and_original_tls_hostname(
        self,
    ) -> None:
        """A rebinding hostname cannot change the TCP destination."""
        stream = Mock()
        stream.send.side_effect = len
        stream.recv.side_effect = [
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n"
            b"Connection: close\r\n\r\nmedia",
        ]
        stream.selected_alpn_protocol.return_value = None
        with (
            patch(
                "socket.getaddrinfo",
                side_effect=[
                    _addresses("93.184.216.34"),
                    _addresses("127.0.0.1"),
                ],
            ) as dns,
            patch("socket.create_connection", return_value=stream) as connect,
            patch.object(
                ssl.SSLContext,
                "wrap_socket",
                autospec=True,
                return_value=stream,
            ) as wrap,
        ):
            self.assertEqual(
                _download_media("https://media.example/file"),
                b"media",
            )
        dns.assert_called_once_with(
            "media.example",
            443,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        connect.assert_called_once_with(
            ("93.184.216.34", 443),
            30,
            source_address=None,
        )
        self.assertEqual(
            wrap.call_args.kwargs["server_hostname"],
            "media.example",
        )
        context = wrap.call_args.args[0]
        self.assertEqual(
            (context.verify_mode, context.check_hostname),
            (ssl.CERT_REQUIRED, True),
        )
        self.assertIn(
            b"Host: media.example\r\n",
            stream.send.call_args_list[0].args[0],
        )
        stream.close.assert_called()


class FormatterURLTest(IsolatedAsyncioTestCase):
    """Reject private media before issuing an outbound request."""

    async def test_private_pdf_and_audio_urls(self) -> None:
        """Neither PDF nor audio input may fetch a loopback endpoint."""
        with patch("httpx.HTTPTransport.handle_request") as send:
            for media_type in ("application/pdf", "audio/wav"):
                with self.subTest(media_type=media_type):
                    message = UserMsg(
                        name="user",
                        content=[
                            DataBlock(
                                source=URLSource(
                                    url="http://127.0.0.1/document",
                                    media_type=media_type,
                                ),
                            ),
                        ],
                    )
                    with self.assertRaises(ValueError):
                        await OpenAIChatFormatter().format([message])
            send.assert_not_called()

    async def test_public_pdf_and_audio_urls(self) -> None:
        """Allowed downloads retain the full provider content structure."""
        for media_type, expected in (
            (
                "application/pdf",
                {
                    "type": "file",
                    "file": {
                        "filename": "document.pdf",
                        "file_data": "data:application/pdf;base64,bWVkaWE=",
                    },
                },
            ),
            (
                "audio/wav",
                {
                    "type": "input_audio",
                    "input_audio": {"data": "bWVkaWE=", "format": "wav"},
                },
            ),
        ):
            with (
                self.subTest(media_type=media_type),
                patch(
                    "socket.getaddrinfo",
                    return_value=_addresses("93.184.216.34"),
                ),
                patch(
                    "httpx.HTTPTransport.handle_request",
                    return_value=httpx.Response(200, content=b"media"),
                ),
            ):
                message = UserMsg(
                    name="user",
                    content=[
                        DataBlock(
                            source=URLSource(
                                url="https://media.example/file",
                                media_type=media_type,
                            ),
                        ),
                    ],
                )
                self.assertEqual(
                    await OpenAIChatFormatter().format([message]),
                    [{"role": "user", "name": "user", "content": [expected]}],
                )

    async def test_all_downloading_formatters_reject_private_media(
        self,
    ) -> None:
        """Chat and multiagent entry points enforce the shared URL policy."""
        cases = (
            (OpenAIChatFormatter, "application/pdf"),
            (OpenAIMultiAgentFormatter, "audio/wav"),
            (AnthropicChatFormatter, "application/pdf"),
            (AnthropicMultiAgentFormatter, "image/png"),
            (GeminiChatFormatter, "video/mp4"),
            (GeminiMultiAgentFormatter, "audio/wav"),
            (OllamaChatFormatter, "image/png"),
            (OllamaMultiAgentFormatter, "image/png"),
            (MoonshotChatFormatter, "image/png"),
            (MoonshotMultiAgentFormatter, "video/mp4"),
        )
        with patch("httpx.HTTPTransport.handle_request") as send:
            for formatter, media_type in cases:
                with self.subTest(formatter=formatter.__name__):
                    message = UserMsg(
                        name="user",
                        content=[
                            DataBlock(
                                source=URLSource(
                                    url="http://127.0.0.1/media",
                                    media_type=media_type,
                                ),
                            ),
                        ],
                    )
                    with self.assertRaisesRegex(ValueError, "public IP"):
                        await formatter().format([message])
            send.assert_not_called()
