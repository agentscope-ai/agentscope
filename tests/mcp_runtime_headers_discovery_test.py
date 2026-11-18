# -*- coding: utf-8 -*-
"""Header-scoped tool discovery through a real Streamable HTTP MCP server."""
import asyncio
import socket
from collections import defaultdict
from typing import Any, Coroutine
from unittest.async_case import IsolatedAsyncioTestCase

import uvicorn
from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import ListToolsResult, Tool

from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.tool import MCPTool


def _expected_schema(tenant: str) -> dict[str, Any]:
    """Describe the complete schema expected for a tenant."""
    return {
        "type": "object",
        "properties": {f"query_{tenant}": {"type": "string"}},
        "required": [f"query_{tenant}"],
    }


class RuntimeHeadersDiscoveryTest(IsolatedAsyncioTestCase):
    """A rotated header must not leave a previous caller's tool catalogue."""

    async def asyncSetUp(self) -> None:
        """Start an SDK server on a reserved loopback socket."""
        self.gates: dict[
            str,
            list[tuple[asyncio.Event, asyncio.Event]],
        ] = defaultdict(list)
        self.releases: list[asyncio.Event] = []
        self.callers: list[asyncio.Task[Any]] = []
        self.clients: list[MCPClient] = []
        self.tool_requests: list[str] = []
        mcp = MCPServer("header-catalogue", middleware=[self._catalogue])

        @mcp.tool()
        async def shared(query_A: str = "", query_B: str = "") -> str:
            """Provide an executable tool for overlapping connections."""
            return query_A or query_B

        ready = asyncio.Event()

        class ReadyServer(uvicorn.Server):
            """Signal that lifespan and the bound listener are ready."""

            async def startup(
                self,
                sockets: list[socket.socket] | None = None,
            ) -> None:
                """Notify the test once Uvicorn has opened its listener."""
                await super().startup(sockets=sockets)
                ready.set()

        self.socket = socket.socket()
        self.addCleanup(self.socket.close)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.setblocking(False)
        port = self.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        app = mcp.streamable_http_app(
            stateless_http=True,
            json_response=True,
        )
        self.server = ReadyServer(
            uvicorn.Config(app, log_level="error", access_log=False),
        )
        self.server_task = asyncio.create_task(
            self.server.serve(sockets=[self.socket]),
        )
        self.addAsyncCleanup(self._stop)
        await asyncio.wait_for(ready.wait(), 5)
        self.assertTrue(self.server.started)

    async def _stop(self) -> None:
        """Drain clients before stopping the server, even after a failure."""
        for release in self.releases:
            release.set()
        for task in self.callers:
            if not task.done():
                task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*self.callers, return_exceptions=True),
                5,
            )
            for client in self.clients:
                if client.is_connected:
                    await asyncio.wait_for(client.close(), 5)
        finally:
            # Uvicorn defines this shutdown flag on its server instance.
            # pylint: disable-next=attribute-defined-outside-init
            self.server.should_exit = True
            await asyncio.wait_for(self.server_task, 5)

    async def _catalogue(
        self,
        context: ServerRequestContext[Any],
        call_next: CallNext,
    ) -> HandlerResult:
        """Snapshot the request's tenant before pausing its response."""
        tenant = context.request.headers.get("x-tenant", "A")
        result = None
        if context.method == "tools/list":
            self.tool_requests.append(tenant)
            result = ListToolsResult(
                tools=[
                    Tool(
                        name="shared",
                        description=f"Tenant {tenant}",
                        input_schema={
                            "type": "object",
                            "properties": {
                                f"query_{tenant}": {"type": "string"},
                            },
                            "required": [f"query_{tenant}"],
                        },
                    ),
                    Tool(
                        name=f"only_{tenant}",
                        input_schema={"type": "object"},
                    ),
                ],
            )
        if self.gates[context.method]:
            entered, release = self.gates[context.method].pop(0)
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return result if result is not None else await call_next(context)

    def _gate(self, method: str) -> tuple[asyncio.Event, asyncio.Event]:
        """Pause the next request of a method after capturing its input."""
        entered, release = asyncio.Event(), asyncio.Event()
        self.gates[method].append((entered, release))
        self.releases.append(release)
        return entered, release

    def _start(self, operation: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Track concurrent callers so failed assertions cannot leak them."""
        task = asyncio.create_task(operation)
        self.callers.append(task)
        return task

    async def _client(self, stateful: bool = False) -> MCPClient:
        """Use A as the static header restored when overrides are cleared."""
        client = MCPClient(
            name="header_catalogue",
            is_stateful=stateful,
            mcp_config=HttpMCPConfig(
                url=self.url,
                headers={"X-Tenant": "A"},
            ),
        )
        self.clients.append(client)
        if stateful:
            await asyncio.wait_for(client.connect(), 5)
        return client

    def _assert_tool(self, tool: MCPTool, tenant: str) -> None:
        """Compare the wrapped tool's complete public descriptor."""
        self.assertEqual(
            (tool.name, tool.description, tool.input_schema),
            (
                "mcp__header_catalogue__shared",
                f"Tenant {tenant}",
                _expected_schema(tenant),
            ),
        )

    def _assert_catalogue(self, tools: list[Tool], tenant: str) -> None:
        """Compare every descriptor, including catalogue size and order."""
        self.assertEqual(
            [(t.name, t.description, t.input_schema) for t in tools],
            [
                ("shared", f"Tenant {tenant}", _expected_schema(tenant)),
                (f"only_{tenant}", None, {"type": "object"}),
            ],
        )

    async def test_cached_lookup_refreshes_after_rotation_and_clear(
        self,
    ) -> None:
        """New lookups update names and schemas in both connection modes."""
        for stateful in (False, True):
            with self.subTest(stateful=stateful):
                self.tool_requests.clear()
                client = await self._client(stateful)
                original = await client.get_tool("shared")
                self._assert_tool(original, "A")
                for headers, tenant, removed in (
                    ({"X-Tenant": "B"}, "B", "A"),
                    ({}, "A", "B"),
                    ({"X-Tenant": "A", "x-tenant": "B"}, "A", "B"),
                    ({"X-Tenant": "B", "x-tenant": "A"}, "B", "A"),
                ):
                    await client.set_runtime_headers(headers)
                    self._assert_tool(await client.get_tool("shared"), tenant)
                    self.assertEqual(
                        (await client.get_tool(f"only_{tenant}")).name,
                        f"mcp__header_catalogue__only_{tenant}",
                    )
                    with self.assertRaises(ValueError):
                        await client.get_tool(f"only_{removed}")
                self.assertEqual(self.tool_requests, ["A", "B", "A", "A", "B"])
                # Previously returned wrappers are snapshots, not live objects.
                self._assert_tool(original, "A")

    async def test_equivalent_and_invalid_updates_preserve_catalogue(
        self,
    ) -> None:
        """Only effective header changes need another tools/list request."""
        for stateful in (False, True):
            with self.subTest(stateful=stateful):
                client = await self._client(stateful)
                await client.get_tool("shared")
                requests = len(self.tool_requests)
                for headers in (
                    {"X-Tenant": "A"},
                    {"x-tenant": "A"},
                    {},
                ):
                    await client.set_runtime_headers(headers)
                    self._assert_tool(await client.get_tool("shared"), "A")
                    self.assertEqual(len(self.tool_requests), requests)
                await client.set_runtime_headers(
                    {"X-Tenant": "B", "X-Extra": "unchanged"},
                )
                # Explicit listing also makes this compatibility test useful
                # on the baseline, where rotation itself does not invalidate.
                await client.list_raw_tools()
                requests = len(self.tool_requests)
                await client.set_runtime_headers(
                    {"x-extra": "unchanged", "x-tenant": "B"},
                )
                for headers in (
                    {"X-Tenant": "A", "Host": "invalid"},
                    {"X-Tenant": "A\r\nInjected: yes"},
                ):
                    with self.assertRaises(ValueError):
                        await client.set_runtime_headers(headers)
                self._assert_tool(await client.get_tool("shared"), "B")
                self.assertEqual(len(self.tool_requests), requests)
                # Verify the retained headers on the wire, as well as cache.
                self._assert_catalogue(await client.list_raw_tools(), "B")

    async def test_rotation_fences_shared_stateless_discovery(self) -> None:
        """An old response cannot populate the new header's shared cache."""
        client = await self._client()
        entered, release = self._gate("tools/list")
        first = self._start(client.list_raw_tools())
        await asyncio.wait_for(entered.wait(), 5)
        await client.set_runtime_headers({"X-Tenant": "B"})
        joined = asyncio.Event()

        async def follow() -> Any:
            joined.set()
            return await client.get_tool("shared")

        second = self._start(follow())
        await asyncio.wait_for(joined.wait(), 5)
        release.set()
        raw, wrapped = await asyncio.wait_for(
            asyncio.gather(first, second),
            5,
        )
        self._assert_catalogue(raw, "B")
        self._assert_tool(wrapped, "B")
        self._assert_tool(await client.get_tool("shared"), "B")
        self.assertEqual(self.tool_requests, ["A", "B"])

    async def test_rotation_during_handshake_with_overlapping_tool_call(
        self,
    ) -> None:
        """Discovery must reject old headers even before its client enters."""
        client = await self._client()
        existing = await client.get_tool("shared")
        entered, release = self._gate("server/discover")
        discovery = self._start(client.list_raw_tools())
        await asyncio.wait_for(entered.wait(), 5)
        called, finish = self._gate("tools/call")
        invocation = self._start(existing(query_A="already-in-flight"))
        await asyncio.wait_for(called.wait(), 5)
        await client.set_runtime_headers({"X-Tenant": "B"})
        release.set()
        tools = await asyncio.wait_for(discovery, 5)
        self._assert_catalogue(tools, "B")
        self._assert_tool(await client.get_tool("shared"), "B")
        self.assertEqual(self.tool_requests, ["A", "A", "B"])
        finish.set()
        result = await asyncio.wait_for(invocation, 5)
        self.assertEqual(
            [(block.type, block.text) for block in result.content],
            [("text", "already-in-flight")],
        )

    async def test_repeated_rotation_is_bounded_and_lookup_can_recover(
        self,
    ) -> None:
        """Two invalidated responses fail without caching either catalogue."""
        for latest in ("C", "A"):
            with self.subTest(latest=latest):
                self.tool_requests.clear()
                client = await self._client()
                gates = [self._gate("tools/list") for _ in range(2)]
                discovery = self._start(client.list_raw_tools())
                for (entered, release), tenant in zip(gates, ("B", latest)):
                    arrival = self._start(entered.wait())
                    await asyncio.wait(
                        (arrival, discovery),
                        timeout=5,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    self.assertTrue(
                        entered.is_set(),
                        "Discovery returned an invalidated catalogue "
                        "without retrying",
                    )
                    await client.set_runtime_headers({"X-Tenant": tenant})
                    release.set()
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    await asyncio.wait_for(discovery, 5)
                self.assertEqual(self.tool_requests, ["A", "B"])
                self._assert_tool(await client.get_tool("shared"), latest)
                self.assertEqual(self.tool_requests, ["A", "B", latest])
