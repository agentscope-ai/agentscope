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


class MCPRuntimeHeadersTest(IsolatedAsyncioTestCase):
    """Runtime headers are request-scoped, not serialized config."""

    async def test_stateful_streamable_http_uses_runtime_headers(self) -> None:
        """One live HTTP client sees each complete replacement map."""
        captured: dict[str, Any] = {}

        @asynccontextmanager
        async def fake_streamable_http_client(
            url: str,
            *,
            http_client: httpx.AsyncClient,
        ) -> AsyncGenerator[tuple[object, object, object], None]:
            captured["url"] = url
            captured["http_client"] = http_client
            yield object(), object(), object()

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

        with patch(
            "agentscope.mcp._mcp_client.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with client._create_http_client():
                http_client = captured["http_client"]
                request = http_client.build_request(
                    "POST",
                    "https://example.com/mcp",
                )
                for hook in http_client.event_hooks["request"]:
                    await hook(request)
                first = {
                    key: request.headers[key]
                    for key in (
                        "Authorization",
                        "X-Static",
                        "X-Runtime",
                    )
                    if key in request.headers
                }

                await client.set_runtime_headers(
                    {
                        "Authorization": "Bearer runtime",
                        "X-Runtime": "first",
                    },
                )
                request = http_client.build_request(
                    "POST",
                    "https://example.com/mcp",
                )
                for hook in http_client.event_hooks["request"]:
                    await hook(request)
                second = {
                    key: request.headers[key]
                    for key in (
                        "Authorization",
                        "X-Static",
                        "X-Runtime",
                    )
                    if key in request.headers
                }

                await client.set_runtime_headers(
                    {
                        "Authorization": "Bearer replacement",
                    },
                )
                request = http_client.build_request(
                    "POST",
                    "https://example.com/mcp",
                )
                for hook in http_client.event_hooks["request"]:
                    await hook(request)
                third = {
                    key: request.headers[key]
                    for key in (
                        "Authorization",
                        "X-Static",
                        "X-Runtime",
                    )
                    if key in request.headers
                }

                await client.set_runtime_headers({})
                request = http_client.build_request(
                    "POST",
                    "https://example.com/mcp",
                )
                for hook in http_client.event_hooks["request"]:
                    await hook(request)
                cleared = {
                    key: request.headers[key]
                    for key in (
                        "Authorization",
                        "X-Static",
                        "X-Runtime",
                    )
                    if key in request.headers
                }

        self.assertEqual(captured["url"], "https://example.com/mcp")
        self.assertEqual(
            first,
            {
                "Authorization": "Bearer static",
                "X-Static": "static",
            },
        )
        self.assertEqual(
            second,
            {
                "Authorization": "Bearer runtime",
                "X-Static": "static",
                "X-Runtime": "first",
            },
        )
        self.assertEqual(
            third,
            {
                "Authorization": "Bearer replacement",
                "X-Static": "static",
            },
        )
        self.assertEqual(
            cleared,
            {
                "Authorization": "Bearer static",
                "X-Static": "static",
            },
        )
        self.assertEqual(
            {
                "follow_redirects": http_client.follow_redirects,
                "timeout": http_client.timeout.as_dict(),
            },
            {
                "follow_redirects": False,
                "timeout": {
                    "connect": 30.0,
                    "read": 30.0,
                    "write": 30.0,
                    "pool": 30.0,
                },
            },
        )

    async def test_streamable_http_preserves_mcp_default_fallback(
        self,
    ) -> None:
        """An unconfigured client retains the MCP SDK HTTP defaults."""
        captured: dict[str, httpx.AsyncClient] = {}

        @asynccontextmanager
        async def fake_streamable_http_client(
            url: str,
            *,
            http_client: httpx.AsyncClient,
        ) -> AsyncGenerator[tuple[object, object, object], None]:
            del url
            captured["http_client"] = http_client
            yield object(), object(), object()

        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                url="https://example.com/mcp",
                timeout=None,
            ),
        )

        with patch(
            "agentscope.mcp._mcp_client.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with client._create_http_client():
                http_client = captured["http_client"]

        self.assertEqual(
            {
                "follow_redirects": http_client.follow_redirects,
                "timeout": http_client.timeout.as_dict(),
            },
            {
                "follow_redirects": True,
                "timeout": {
                    "connect": 30.0,
                    "read": 300.0,
                    "write": 30.0,
                    "pool": 30.0,
                },
            },
        )

    async def test_runtime_headers_stay_on_configured_origin(self) -> None:
        """Cross-origin redirects never receive runtime credentials."""
        received: list[dict[str, str | None]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            received.append(
                {
                    "url": str(request.url),
                    "authorization": request.headers.get("Authorization"),
                    "runtime": request.headers.get("X-Runtime"),
                },
            )
            if request.url.host == "example.com":
                await client.set_runtime_headers({})
                return httpx.Response(
                    302,
                    headers={"Location": "https://other.example/mcp"},
                )
            return httpx.Response(200)

        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )
        await client.set_runtime_headers(
            {
                "Authorization": "Bearer runtime",
                "X-Runtime": "runtime",
            },
        )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
            event_hooks={"request": [client._inject_runtime_headers]},
        ) as http_client:
            response = await http_client.get("https://example.com/mcp")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            received,
            [
                {
                    "url": "https://example.com/mcp",
                    "authorization": "Bearer runtime",
                    "runtime": "runtime",
                },
                {
                    "url": "https://other.example/mcp",
                    "authorization": None,
                    "runtime": None,
                },
            ],
        )

    async def test_stateless_streamable_http_uses_runtime_headers(
        self,
    ) -> None:
        """A temporary HTTP client reads current runtime headers."""
        captured: dict[str, httpx.AsyncClient] = {}

        @asynccontextmanager
        async def fake_streamable_http_client(
            url: str,
            *,
            http_client: httpx.AsyncClient,
        ) -> AsyncGenerator[tuple[object, object, object], None]:
            del url
            captured["http_client"] = http_client
            yield object(), object(), object()

        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )
        await client.set_runtime_headers(
            {"Authorization": "Bearer runtime"},
        )

        with patch(
            "agentscope.mcp._mcp_client.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with client._create_http_client():
                http_client = captured["http_client"]
                request = http_client.build_request(
                    "POST",
                    "https://example.com/mcp",
                )
                for hook in http_client.event_hooks["request"]:
                    await hook(request)

        self.assertEqual(
            request.headers["Authorization"],
            "Bearer runtime",
        )

    async def test_stateful_allows_runtime_headers_before_connect(
        self,
    ) -> None:
        """A local stateful client accepts headers before connecting."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=True,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )

        await client.set_runtime_headers(
            {"Authorization": "Bearer runtime"},
        )

        self.assertEqual(
            {
                "is_connected": client.is_connected,
                "runtime_headers": client._runtime_headers,
            },
            {
                "is_connected": False,
                "runtime_headers": {
                    "Authorization": "Bearer runtime",
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

        await client.set_runtime_headers(
            {"Authorization": "Bearer runtime"},
        )

        self.assertEqual(
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

    async def test_runtime_headers_reject_transport_headers(self) -> None:
        """Callers cannot override headers owned by the MCP transport."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )

        with self.assertRaisesRegex(
            ValueError,
            "Mcp-Session-Id",
        ):
            await client.set_runtime_headers(
                {"Mcp-Session-Id": "forbidden"},
            )

    async def test_runtime_headers_reject_sse(self) -> None:
        """SSE headers remain fixed for the lifetime of the stream."""
        for url in (
            "https://example.com/sse",
            "https://example.com/messages/",
        ):
            with self.subTest(url=url):
                client = MCPClient(
                    name="runtime_headers",
                    is_stateful=True,
                    mcp_config=HttpMCPConfig(url=url),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "only Streamable HTTP",
                ):
                    await client.set_runtime_headers(
                        {"Authorization": "Bearer runtime"},
                    )

    async def test_runtime_headers_reject_invalid_wire_values(self) -> None:
        """Invalid names and values fail before an HTTP request is sent."""
        client = MCPClient(
            name="runtime_headers",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )

        for headers, invalid_name in (
            ({"Bad Header": "sensitive-value"}, "Bad Header"),
            ({"X-Unsafe": "sensitive\nvalue"}, "X-Unsafe"),
            ({"X-Unicode": "sensitive-\u00e9"}, "X-Unicode"),
        ):
            with self.subTest(headers=list(headers)):
                with self.assertRaisesRegex(
                    ValueError,
                    invalid_name,
                ) as raised:
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
        self.state.clients[("agent", "session")] = {
            "remote": self.mcp,
        }
        self.client = TestClient(_build_app(self.state))

    def test_update_runtime_headers(self) -> None:
        """PUT replaces live headers and returns no sensitive body."""
        response = self.client.put(
            "/mcps/remote/runtime-headers",
            params={"agent_id": "agent", "session_id": "session"},
            json={
                "headers": {
                    "Authorization": "Bearer runtime",
                },
            },
        )

        self.assertEqual(
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
                "runtime_headers": {
                    "Authorization": "Bearer runtime",
                },
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
        """Validation failures identify only the forbidden name."""
        response = self.client.put(
            "/mcps/remote/runtime-headers",
            params={"agent_id": "agent", "session_id": "session"},
            json={
                "headers": {
                    "Mcp-Session-Id": "sensitive-value",
                },
            },
        )

        self.assertEqual(
            {
                "status_code": response.status_code,
                "body": response.json(),
                "runtime_headers": self.mcp._runtime_headers,
            },
            {
                "status_code": 400,
                "body": {
                    "detail": (
                        "Runtime header 'Mcp-Session-Id' is owned by "
                        "the transport."
                    ),
                },
                "runtime_headers": {},
            },
        )
        self.assertNotIn("sensitive-value", response.text)

    def test_update_runtime_headers_holds_registry_lock(self) -> None:
        """Header updates serialize with client registration changes."""
        lock_states: list[bool] = []

        async def capture_lock_state(
            _client: MCPClient,
            _headers: dict[str, str],
        ) -> None:
            lock_states.append(self.state.lock.locked())

        with patch.object(
            MCPClient,
            "set_runtime_headers",
            capture_lock_state,
        ):
            response = self.client.put(
                "/mcps/remote/runtime-headers",
                params={"agent_id": "agent", "session_id": "session"},
                json={"headers": {"Authorization": "Bearer runtime"}},
            )

        self.assertEqual(
            {
                "status_code": response.status_code,
                "lock_states": lock_states,
            },
            {
                "status_code": 204,
                "lock_states": [True],
            },
        )


class GatewayMCPClientRuntimeHeadersTest(IsolatedAsyncioTestCase):
    """The host-side proxy forwards runtime header updates."""

    async def test_proxy_updates_connected_gateway_client(self) -> None:
        """The proxy sends one scoped PUT without changing its spec."""
        gateway = GatewayClient(
            backend=object(),  # type: ignore[arg-type]
            gateway_port=5600,
        )
        gateway.exec_request = AsyncMock(  # type: ignore[method-assign]
            return_value=(204, b""),
        )
        client = gateway.make_client(
            MCPClient(
                name="remote",
                is_stateful=False,
                mcp_config=HttpMCPConfig(
                    url="https://example.com/mcp",
                ),
            ).model_dump(mode="json"),
            agent_id="agent",
            session_id="session",
            connected=True,
        )

        await client.set_runtime_headers(
            {"Authorization": "Bearer runtime"},
        )

        gateway.exec_request.assert_awaited_once_with(
            "PUT",
            "/mcps/remote/runtime-headers",
            params={"agent_id": "agent", "session_id": "session"},
            body={
                "headers": {
                    "Authorization": "Bearer runtime",
                },
            },
        )
        self.assertEqual(
            client.model_dump(mode="json")["mcp_config"]["headers"],
            None,
        )

    async def test_proxy_rejects_update_before_connect(self) -> None:
        """First-version updates require an existing gateway client."""
        gateway = GatewayClient(
            backend=object(),  # type: ignore[arg-type]
            gateway_port=5600,
        )
        client = gateway.make_client(
            MCPClient(
                name="remote",
                is_stateful=False,
                mcp_config=HttpMCPConfig(
                    url="https://example.com/mcp",
                ),
            ).model_dump(mode="json"),
        )

        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await client.set_runtime_headers(
                {"Authorization": "Bearer runtime"},
            )

    async def test_proxy_maps_gateway_validation_error(self) -> None:
        """Gateway validation failures match the local exception type."""
        gateway = GatewayClient(
            backend=object(),  # type: ignore[arg-type]
            gateway_port=5600,
        )
        gateway.exec_request = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                400,
                b'{"detail":"Runtime header is invalid."}',
            ),
        )
        client = gateway.make_client(
            MCPClient(
                name="remote",
                is_stateful=False,
                mcp_config=HttpMCPConfig(
                    url="https://example.com/mcp",
                ),
            ).model_dump(mode="json"),
            connected=True,
        )

        with self.assertRaisesRegex(
            ValueError,
            "HTTP 400: Runtime header is invalid",
        ):
            await client.set_runtime_headers(
                {"Bad Header": "value"},
            )


if __name__ == "__main__":
    unittest.main()
