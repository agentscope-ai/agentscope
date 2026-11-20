# -*- coding: utf-8 -*-
"""Host-side gateway discovery must follow acknowledged header updates."""
# pylint: disable=protected-access
import asyncio
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from mcp.types import Tool

from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.workspace._gateway_client import (
    GatewayClient,
    GatewayMCPClient,
    GatewayMCPTool,
)


def _schema(tenant: str) -> dict[str, Any]:
    """Return a complete tenant-specific tool schema."""
    return {
        "type": "object",
        "properties": {f"query_{tenant}": {"type": "string"}},
        "required": [f"query_{tenant}"],
    }


def _catalogue(tenant: str) -> list[Tool]:
    """Include a shared name and a tool available to only one tenant."""
    return [
        Tool(
            name="shared",
            description=f"Tenant {tenant}",
            input_schema=_schema(tenant),
        ),
        Tool(
            name=f"only_{tenant}",
            description=f"Only {tenant}",
            input_schema={"type": "object", "properties": {}, "required": []},
        ),
    ]


class _CatalogGateway(GatewayClient):
    """Model the gateway response boundary without mocking the proxy."""

    def __init__(self, stateful: bool = False) -> None:
        super().__init__(
            backend=object(),  # type: ignore[arg-type]
            gateway_port=5600,
        )
        self.spec = MCPClient(
            name="remote",
            is_stateful=stateful,
            mcp_config=HttpMCPConfig(
                url="https://example.com/mcp",
                headers={"X-Tenant": "A"},
            ),
        ).model_dump(mode="json")
        self.headers: dict[str, str] = {}
        self.listed: list[str] = []
        self.requests: list[tuple[str, str, Any]] = []
        self.gates: list[tuple[asyncio.Event, asyncio.Event]] = []
        self.update_gate: tuple[asyncio.Event, asyncio.Event] | None = None
        self.update_failure: tuple[int, bytes] | OSError | None = None
        self.list_failure: tuple[int, bytes] | None = None

    def pause_next_list(self) -> tuple[asyncio.Event, asyncio.Event]:
        """Pause delivery after the gateway has captured a list response."""
        gate = asyncio.Event(), asyncio.Event()
        self.gates.append(gate)
        return gate

    async def exec_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
        include_auth: bool = True,
    ) -> tuple[int, bytes]:
        """Handle scoped listing and header replacement like the gateway."""
        del include_auth
        if params != {"agent_id": "agent", "session_id": "session"}:
            raise AssertionError(f"Unexpected scope: {params!r}")
        self.requests.append((method, path, body))
        if method == "GET" and path == "/mcps":
            # GET /mcps intentionally excludes private runtime headers.
            return 200, json.dumps([self.spec]).encode()
        if method == "PUT" and path == "/mcps/remote/runtime-headers":
            failure = self.update_failure
            if isinstance(failure, OSError):
                raise OSError(str(failure))
            if failure is not None:
                return failure
            self.headers = dict(body["headers"])
            if self.update_gate is not None:
                entered, release = self.update_gate
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return 204, b""
        if method == "GET" and path == "/mcps/remote/tools":
            if self.list_failure is not None:
                return self.list_failure
            normalized = {
                key.lower(): value for key, value in self.headers.items()
            }
            tenant = normalized.get("x-tenant", "A")
            self.listed.append(tenant)
            response = json.dumps(
                [tool.model_dump(mode="json") for tool in _catalogue(tenant)],
            ).encode()
            if self.gates:
                entered, release = self.gates.pop(0)
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return 200, response
        raise AssertionError(f"Unexpected request: {method} {path}")


class GatewayHeadersDiscoveryTest(IsolatedAsyncioTestCase):
    """A proxy must not reuse a catalog from before a successful update."""

    async def _proxy(
        self,
        stateful: bool = False,
    ) -> tuple[_CatalogGateway, GatewayMCPClient]:
        """Build a freshly listed proxy using the public gateway API."""
        gateway = _CatalogGateway(stateful)
        clients = await gateway.list_mcps("agent", "session")
        self.assertEqual(len(clients), 1)
        return gateway, clients[0]

    def _assert_tool(self, tool: GatewayMCPTool, tenant: str) -> None:
        """Compare the whole model-facing descriptor."""
        self.assertEqual(
            (tool.name, tool.description, tool.input_schema),
            ("mcp__remote__shared", f"Tenant {tenant}", _schema(tenant)),
        )

    def _assert_catalogue(self, tools: list[Tool], tenant: str) -> None:
        """Compare names, descriptions and complete schemas in order."""
        self.assertEqual(
            [tool.model_dump() for tool in tools],
            [tool.model_dump() for tool in _catalogue(tenant)],
        )

    async def test_success_refreshes_cached_schema_and_available_names(
        self,
    ) -> None:
        """Both connection modes expose the acknowledged tenant's tools."""
        for stateful in (False, True):
            with self.subTest(stateful=stateful):
                gateway, proxy = await self._proxy(stateful)
                spec = proxy.model_dump(mode="json")
                previous = await proxy.get_tool("shared")
                await proxy.set_runtime_headers({"X-Tenant": "B"})

                self._assert_tool(await proxy.get_tool("shared"), "B")
                self._assert_tool(previous, "A")
                self.assertEqual(
                    (await proxy.get_tool("only_B")).name,
                    "mcp__remote__only_B",
                )
                with self.assertRaisesRegex(ValueError, "only_A"):
                    await proxy.get_tool("only_A")
                self.assertEqual(gateway.listed, ["A", "B"])
                self.assertEqual(proxy.model_dump(mode="json"), spec)

    async def test_fresh_proxy_clears_unknown_gateway_override(self) -> None:
        """Clearing an override works even when a fresh proxy never saw it."""
        gateway = _CatalogGateway()
        gateway.headers = {"X-Tenant": "B"}
        proxy = (await gateway.list_mcps("agent", "session"))[0]
        self.assertEqual(proxy._runtime_headers, {})
        self._assert_tool(await proxy.get_tool("shared"), "B")

        await proxy.set_runtime_headers({})

        self._assert_tool(await proxy.get_tool("shared"), "A")
        self.assertEqual(gateway.listed, ["B", "A"])

    async def test_every_acknowledgement_refreshes_the_proxy(self) -> None:
        """An equivalent successful PUT still invalidates host knowledge."""
        gateway, proxy = await self._proxy()
        await proxy.set_runtime_headers({"X-Tenant": "B"})
        self._assert_tool(await proxy.get_tool("shared"), "B")
        await proxy.set_runtime_headers({"x-tenant": "B"})

        self._assert_tool(await proxy.get_tool("shared"), "B")
        self.assertEqual(gateway.listed, ["B", "B"])

    async def test_rejected_updates_preserve_the_current_catalogue(
        self,
    ) -> None:
        """Failed requests neither clear cached tools nor save new headers."""
        for failure, error in (
            ((400, b'{"detail":"invalid header"}'), ValueError),
            ((404, b'{"detail":"missing"}'), RuntimeError),
            ((500, b'{"detail":"upstream error"}'), RuntimeError),
            (OSError("transport failed"), OSError),
        ):
            with self.subTest(failure=failure):
                gateway, proxy = await self._proxy()
                await proxy.set_runtime_headers({"X-Tenant": "B"})
                previous = await proxy.get_tool("shared")
                gateway.update_failure = failure
                with self.assertRaises(error):
                    await proxy.set_runtime_headers({"X-Tenant": "C"})

                self._assert_tool(await proxy.get_tool("shared"), "B")
                self._assert_tool(previous, "B")
                self.assertEqual(proxy._runtime_headers, {"X-Tenant": "B"})
                self.assertEqual(gateway.listed, ["B"])

    async def test_remembered_headers_match_the_acknowledged_request(
        self,
    ) -> None:
        """Caller mutation during the PUT cannot alter reconnect headers."""
        gateway, proxy = await self._proxy()
        entered, release = asyncio.Event(), asyncio.Event()
        gateway.update_gate = entered, release
        headers = {"X-Tenant": "B"}
        update = asyncio.create_task(proxy.set_runtime_headers(headers))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            headers["X-Tenant"] = "C"
            release.set()
            await asyncio.wait_for(update, 5)

            self._assert_tool(await proxy.get_tool("shared"), "B")
            self.assertEqual(proxy._runtime_headers, {"X-Tenant": "B"})
            self.assertEqual(gateway.headers, {"X-Tenant": "B"})
        finally:
            release.set()
            await asyncio.gather(update, return_exceptions=True)

    async def test_delayed_old_response_is_retried(self) -> None:
        """A GET completed before the PUT cannot restore stale descriptors."""
        gateway, proxy = await self._proxy()
        entered, release = gateway.pause_next_list()
        listing = asyncio.create_task(proxy.list_raw_tools())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await proxy.set_runtime_headers({"X-Tenant": "B"})
            release.set()
            self._assert_catalogue(await asyncio.wait_for(listing, 5), "B")
            self._assert_tool(await proxy.get_tool("shared"), "B")
            self.assertEqual(gateway.listed, ["A", "B"])
        finally:
            release.set()
            await asyncio.gather(listing, return_exceptions=True)

    async def test_second_invalidation_fails_without_caching_stale_tools(
        self,
    ) -> None:
        """Repeated rotation has a bounded retry and a recoverable failure."""
        gateway, proxy = await self._proxy()
        entered_a, release_a = gateway.pause_next_list()
        entered_b, release_b = gateway.pause_next_list()
        listing = asyncio.create_task(proxy.list_raw_tools())
        try:
            await asyncio.wait_for(entered_a.wait(), 5)
            await proxy.set_runtime_headers({"X-Tenant": "B"})
            release_a.set()
            await asyncio.wait_for(entered_b.wait(), 5)
            await proxy.set_runtime_headers({"X-Tenant": "C"})
            release_b.set()
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(listing, 5)
            self.assertEqual(gateway.listed, ["A", "B"])
            self._assert_tool(await proxy.get_tool("shared"), "C")
            self.assertEqual(gateway.listed, ["A", "B", "C"])
        finally:
            release_a.set()
            release_b.set()
            await asyncio.gather(listing, return_exceptions=True)

    async def test_filters_apply_after_fresh_discovery(self) -> None:
        """Refreshed filtered listings keep direct lookup semantics."""
        gateway, proxy = await self._proxy()
        proxy.enable_tools = ["shared"]
        proxy.disable_tools = ["only_B"]
        await proxy.get_tool("shared")
        await proxy.set_runtime_headers({"X-Tenant": "B"})

        tools = await proxy.list_raw_tools()

        self.assertEqual(
            [tool.model_dump() for tool in tools],
            [_catalogue("B")[0].model_dump()],
        )
        self.assertEqual(
            (await proxy.get_tool("only_B")).name,
            "mcp__remote__only_B",
        )
        self.assertEqual(gateway.listed, ["A", "B"])

    async def test_failed_discovery_does_not_restore_pre_update_cache(
        self,
    ) -> None:
        """A transient GET failure leaves a later lookup free to retry."""
        gateway, proxy = await self._proxy()
        await proxy.get_tool("shared")
        await proxy.set_runtime_headers({"X-Tenant": "B"})
        gateway.list_failure = 503, b'{"detail":"temporarily unavailable"}'
        with self.assertRaisesRegex(RuntimeError, "503"):
            await proxy.get_tool("shared")
        gateway.list_failure = None

        self._assert_tool(await proxy.get_tool("shared"), "B")
        self.assertEqual(gateway.listed, ["A", "B"])
