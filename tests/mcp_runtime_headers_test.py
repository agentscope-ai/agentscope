# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for live MCP HTTP header updates."""
from contextlib import asynccontextmanager
import unittest
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.workspace._gateway_client import GatewayClient
from agentscope.workspace._mcp_gateway._mcp_gateway_app import (
    _State,
    _build_app,
)

_WATCHED = ("Authorization", "X-Static", "X-Runtime", "Mcp-Session-Id")


class MCPRuntimeHeadersTest(IsolatedAsyncioTestCase):
    """Runtime headers are live client state, not serialized config."""

    def setUp(self) -> None:
        """Replace the transport with one exposing its HTTP client."""
        self.seen: dict[str, Any] = {}

        @asynccontextmanager
        async def fake_transport(
            url: str,
            *,
            http_client: httpx.AsyncClient,
        ) -> AsyncGenerator[tuple[object, object, object], None]:
            self.seen.update(url=url, http_client=http_client)
            yield object(), object(), object()

        patcher = patch(
            "agentscope.mcp._mcp_client.streamable_http_client",
            fake_transport,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _outgoing(self, **headers: str) -> dict[str, str]:
        """The watched headers httpx would put on the next request."""
        request = self.seen["http_client"].build_request(
            "POST",
            "https://example.com/mcp",
            headers=headers or None,
        )
        return {
            name: request.headers[name]
            for name in _WATCHED
            if name in request.headers
        }

    async def test_runtime_headers_replace_and_clear(self) -> None:
        """Each call replaces the whole map; an empty one restores config."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=True,
            mcp_config=HttpMCPConfig(
                url="https://example.com/mcp",
                headers={
                    "Authorization": "Bearer static",
                    "X-Static": "static",
                },
            ),
        )

        async with client._create_http_client():
            configured = self._outgoing()

            await client.set_runtime_headers(
                {"Authorization": "Bearer runtime", "X-Runtime": "first"},
            )
            overridden = self._outgoing()

            await client.set_runtime_headers(
                {"Authorization": "Bearer replacement"},
            )
            replaced = self._outgoing()

            await client.set_runtime_headers({})
            cleared = self._outgoing()

        self.assertDictEqual(
            {
                "url": self.seen["url"],
                "configured": configured,
                "overridden": overridden,
                "replaced": replaced,
                "cleared": cleared,
            },
            {
                "url": "https://example.com/mcp",
                "configured": {
                    "Authorization": "Bearer static",
                    "X-Static": "static",
                },
                "overridden": {
                    "Authorization": "Bearer runtime",
                    "X-Static": "static",
                    "X-Runtime": "first",
                },
                "replaced": {
                    "Authorization": "Bearer replacement",
                    "X-Static": "static",
                },
                "cleared": {
                    "Authorization": "Bearer static",
                    "X-Static": "static",
                },
            },
        )

    async def test_headers_set_before_the_client_exists_are_applied(
        self,
    ) -> None:
        """A stateless client picks up headers set before it was built."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )
        await client.set_runtime_headers({"Authorization": "Bearer runtime"})

        async with client._create_http_client():
            outgoing = self._outgoing()

        self.assertDictEqual(
            outgoing,
            {"Authorization": "Bearer runtime"},
        )

    async def test_transport_headers_win_over_runtime_headers(self) -> None:
        """Per-request MCP headers outrank anything set on the client."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )
        await client.set_runtime_headers({"Mcp-Session-Id": "hijacked"})

        async with client._create_http_client():
            outgoing = self._outgoing(**{"mcp-session-id": "from-transport"})

        self.assertDictEqual(
            outgoing,
            {"Mcp-Session-Id": "from-transport"},
        )

    async def test_owning_the_client_keeps_transport_defaults(self) -> None:
        """Taking ownership must not change how the client is configured."""
        defaults: dict[str, Any] = {}
        for label, config in (
            (
                "configured",
                HttpMCPConfig(
                    url="https://example.com/mcp",
                    headers={"X-Static": "static"},
                ),
            ),
            (
                "untimed",
                HttpMCPConfig(url="https://example.com/mcp", timeout=None),
            ),
        ):
            client = MCPClient(
                name="runtime_headers",
                is_stateful=False,
                mcp_config=config,
            )
            async with client._create_http_client():
                http_client = self.seen["http_client"]
                defaults[label] = {
                    "follow_redirects": http_client.follow_redirects,
                    "read_timeout": http_client.timeout.read,
                }

        self.assertDictEqual(
            defaults,
            {
                "configured": {
                    "follow_redirects": False,
                    "read_timeout": 30.0,
                },
                "untimed": {
                    "follow_redirects": True,
                    "read_timeout": 300.0,
                },
            },
        )

    async def test_runtime_headers_are_not_serialized(self) -> None:
        """Live headers never become part of the persisted MCP spec."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                url="https://example.com/mcp",
                headers={"X-Static": "static"},
            ),
        )
        await client.set_runtime_headers({"Authorization": "Bearer runtime"})

        self.assertDictEqual(
            client.model_dump(mode="json"),
            {
                "name": "runtime_headers",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "https://example.com/mcp",
                    "headers": {"X-Static": "static"},
                    "timeout": 30.0,
                },
                "enable_tools": None,
                "disable_tools": None,
                "execution_timeout": None,
            },
        )

    async def test_runtime_headers_reject_sse(self) -> None:
        """SSE headers stay fixed for the lifetime of the stream."""
        for url in (
            "https://example.com/sse?key=secret",
            "https://example.com/messages/",
        ):
            with self.subTest(url=url):
                client = MCPClient(
                    name="runtime_headers",
                    is_stateful=True,
                    mcp_config=HttpMCPConfig(url=url),
                )
                with self.assertRaisesRegex(ValueError, "Streamable HTTP"):
                    await client.set_runtime_headers({"X-Runtime": "value"})

    async def test_runtime_headers_reject_http_owned_names(self) -> None:
        """httpx derives these with setdefault, so a value here would win."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )

        for name in (
            "Host",
            "Content-Length",
            "Transfer-Encoding",
            "connection",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "owned by the HTTP"):
                    await client.set_runtime_headers({name: "hijacked"})

    async def test_runtime_headers_reject_invalid_wire_values(self) -> None:
        """Invalid names and values fail before an HTTP request is sent."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )

        for headers, name in (
            ({"Bad Header": "sensitive-value"}, "Bad Header"),
            ({"X-Unsafe": "sensitive\r\nInjected: 1"}, "X-Unsafe"),
            ({"X-Unicode": "sensitive-é"}, "X-Unicode"),
        ):
            with self.subTest(headers=list(headers)):
                with self.assertRaisesRegex(ValueError, name) as raised:
                    await client.set_runtime_headers(headers)
                self.assertNotIn("sensitive", str(raised.exception))


class GatewayRuntimeHeadersRouteTest(unittest.TestCase):
    """The gateway updates its live client without replacing it."""

    def setUp(self) -> None:
        """Build an app with one registered stateful HTTP client."""
        self.state = _State()
        self.mcp = MCPClient(
            name="remote",
            is_stateful=True,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )
        self.mcp._is_connected = True
        self.state.clients[("agent", "session")] = {"remote": self.mcp}
        self.client = TestClient(_build_app(self.state))

    def test_update_runtime_headers(self) -> None:
        """PUT replaces live headers on the same client instance."""
        response = self.client.put(
            "/mcps/remote/runtime-headers",
            params={"agent_id": "agent", "session_id": "session"},
            json={"headers": {"Authorization": "Bearer runtime"}},
        )

        self.assertDictEqual(
            {
                "status_code": response.status_code,
                "body": response.content,
                "same_client": self.state.clients[("agent", "session")][
                    "remote"
                ]
                is self.mcp,
                "is_connected": self.mcp.is_connected,
                "runtime_headers": self.mcp._runtime_headers,
            },
            {
                "status_code": 204,
                "body": b"",
                "same_client": True,
                "is_connected": True,
                "runtime_headers": {"Authorization": "Bearer runtime"},
            },
        )

    def test_registration_applies_headers_before_connect(self) -> None:
        """The gateway's own handshake must use the rotated credential."""
        at_connect: list[dict[str, str]] = []

        async def capture(client: MCPClient) -> None:
            at_connect.append(dict(client._runtime_headers))

        with patch.object(MCPClient, "connect", capture), patch.object(
            MCPClient,
            "list_raw_tools",
            AsyncMock(return_value=[]),
        ):
            response = self.client.post(
                "/mcps",
                params={"agent_id": "agent", "session_id": "session"},
                json={
                    "name": "fresh",
                    "is_stateful": True,
                    "mcp_config": {
                        "type": "http_mcp",
                        "url": "https://example.com/mcp",
                    },
                    "runtime_headers": {"Authorization": "Bearer runtime"},
                },
            )

        self.assertDictEqual(
            {
                "status_code": response.status_code,
                "headers_at_connect": at_connect,
            },
            {
                "status_code": 200,
                "headers_at_connect": [{"Authorization": "Bearer runtime"}],
            },
        )

    def test_update_runtime_headers_rejects_unknown_client(self) -> None:
        """The update route preserves the gateway's lookup contract."""
        response = self.client.put(
            "/mcps/missing/runtime-headers",
            params={"agent_id": "agent", "session_id": "session"},
            json={"headers": {"Authorization": "Bearer runtime"}},
        )

        self.assertEqual(response.status_code, 404)

    def test_update_runtime_headers_does_not_echo_values(self) -> None:
        """Validation failures identify only the offending name."""
        response = self.client.put(
            "/mcps/remote/runtime-headers",
            params={"agent_id": "agent", "session_id": "session"},
            json={"headers": {"Bad Header": "sensitive-value"}},
        )

        self.assertDictEqual(
            {
                "status_code": response.status_code,
                "body": response.json(),
                "runtime_headers": self.mcp._runtime_headers,
            },
            {
                "status_code": 400,
                "body": {"detail": "Runtime header 'Bad Header' is invalid."},
                "runtime_headers": {},
            },
        )


class GatewayMCPClientRuntimeHeadersTest(IsolatedAsyncioTestCase):
    """The host-side proxy forwards runtime header updates."""

    @staticmethod
    def _proxy(gateway: GatewayClient, connected: bool) -> Any:
        """A proxy for one stateless HTTP MCP on ``gateway``."""
        return gateway.make_client(
            MCPClient(
                name="remote",
                is_stateful=False,
                mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
            ).model_dump(mode="json"),
            agent_id="agent",
            session_id="session",
            connected=connected,
        )

    async def test_proxy_updates_connected_gateway_client(self) -> None:
        """The proxy sends one scoped PUT without changing its spec."""
        gateway = GatewayClient(
            backend=object(),  # type: ignore[arg-type]
            gateway_port=5600,
        )
        gateway.exec_request = AsyncMock(  # type: ignore[method-assign]
            return_value=(204, b""),
        )
        client = self._proxy(gateway, connected=True)

        await client.set_runtime_headers({"Authorization": "Bearer runtime"})

        gateway.exec_request.assert_awaited_once_with(
            "PUT",
            "/mcps/remote/runtime-headers",
            params={"agent_id": "agent", "session_id": "session"},
            body={"headers": {"Authorization": "Bearer runtime"}},
        )
        self.assertDictEqual(
            client.model_dump(mode="json"),
            {
                "name": "remote",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "https://example.com/mcp",
                    "headers": None,
                    "timeout": 30.0,
                },
                "enable_tools": None,
                "disable_tools": None,
                "execution_timeout": None,
            },
        )

    async def test_proxy_registers_with_runtime_headers(self) -> None:
        """The gateway connects during POST, so they must ride along."""
        gateway = GatewayClient(
            backend=object(),  # type: ignore[arg-type]
            gateway_port=5600,
        )
        gateway.exec_request = AsyncMock(  # type: ignore[method-assign]
            return_value=(204, b""),
        )
        client = self._proxy(gateway, connected=True)
        await client.set_runtime_headers({"Authorization": "Bearer runtime"})
        await client.close()

        gateway.exec_request.reset_mock()
        await client.connect()

        gateway.exec_request.assert_awaited_once_with(
            "POST",
            "/mcps",
            params={"agent_id": "agent", "session_id": "session"},
            body={
                "name": "remote",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "https://example.com/mcp",
                    "headers": None,
                    "timeout": 30.0,
                },
                "enable_tools": None,
                "disable_tools": None,
                "execution_timeout": None,
                "runtime_headers": {"Authorization": "Bearer runtime"},
            },
        )

    async def test_proxy_rejects_update_before_connect(self) -> None:
        """Updates require an existing gateway-side client."""
        gateway = GatewayClient(
            backend=object(),  # type: ignore[arg-type]
            gateway_port=5600,
        )
        client = self._proxy(gateway, connected=False)

        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await client.set_runtime_headers({"X-Runtime": "value"})

    async def test_proxy_reports_gateway_errors(self) -> None:
        """400 matches the local exception type; 404 names the cause."""
        for status, body, error, message in (
            (
                400,
                b'{"detail":"Runtime header is invalid."}',
                ValueError,
                "HTTP 400: Runtime header is invalid",
            ),
            (
                404,
                b'{"detail":"Not Found"}',
                RuntimeError,
                "no runtime-headers route",
            ),
        ):
            with self.subTest(status=status):
                gateway = GatewayClient(
                    backend=object(),  # type: ignore[arg-type]
                    gateway_port=5600,
                )
                gateway.exec_request = (  # type: ignore[method-assign]
                    AsyncMock(return_value=(status, body))
                )
                client = self._proxy(gateway, connected=True)

                with self.assertRaisesRegex(error, message):
                    await client.set_runtime_headers({"X-Runtime": "value"})


if __name__ == "__main__":
    unittest.main()
