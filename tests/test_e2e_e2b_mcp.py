# -*- coding: utf-8 -*-
"""E2E test: per-agent MCP isolation via E2BWorkspace.

Requires: ``E2B_API_KEY`` environment variable.
"""
import json
import os
import unittest

from agentscope.workspace import E2BWorkspace
from agentscope.mcp import MCPClient, HttpMCPConfig

_E2B_API_KEY = os.getenv("E2B_API_KEY", "")
_SKIP_REASON = "E2B_API_KEY environment variable is not set"


@unittest.skipUnless(_E2B_API_KEY, _SKIP_REASON)
class TestE2BPerAgentMCP(unittest.IsolatedAsyncioTestCase):
    """Per-agent MCP isolation tests for E2BWorkspace."""

    def _make_mcp(self, name: str) -> MCPClient:
        return MCPClient(
            name=name,
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="http://127.0.0.1:1/mcp"),
        )

    async def asyncSetUp(self) -> None:
        self._ws = E2BWorkspace(
            api_key=_E2B_API_KEY,
            default_mcps=[self._make_mcp("default-fs")],
        )
        await self._ws.initialize()

    async def asyncTearDown(self) -> None:
        await self._ws.close()

    async def test_lazy_clone_from_default_mcps(self) -> None:
        """First list_mcps clones from default_mcps for each agent."""
        mcps_a = await self._ws.list_mcps("agent-A")
        self.assertEqual(len(mcps_a), 1)
        self.assertEqual(mcps_a[0].name, "default-fs")

        mcps_a2 = await self._ws.list_mcps("agent-A")
        self.assertEqual(len(mcps_a2), 1)

        mcps_b = await self._ws.list_mcps("agent-B")
        self.assertEqual(len(mcps_b), 1)
        self.assertEqual(mcps_b[0].name, "default-fs")

    async def test_add_remove_per_agent_isolation(self) -> None:
        """add_mcp / remove_mcp scoped to agent_id."""
        await self._ws.add_mcp("agent-A", self._make_mcp("extra"))
        mcps_a = await self._ws.list_mcps("agent-A")
        self.assertIn("extra", [m.name for m in mcps_a])

        mcps_b = await self._ws.list_mcps("agent-B")
        self.assertNotIn("extra", [m.name for m in mcps_b])

        await self._ws.remove_mcp("agent-A", "extra")
        mcps_a = await self._ws.list_mcps("agent-A")
        self.assertNotIn("extra", [m.name for m in mcps_a])

    async def test_duplicate_same_agent_raises(self) -> None:
        """Duplicate MCP name within same agent raises ValueError."""
        await self._ws.add_mcp("agent-A", self._make_mcp("dup-me"))
        with self.assertRaises(ValueError):
            await self._ws.add_mcp("agent-A", self._make_mcp("dup-me"))

    async def test_persistence_per_agent_format(self) -> None:
        """.mcp file uses {agent_id: [configs]} format."""
        await self._ws.add_mcp("agent-A", self._make_mcp("a-tool"))
        await self._ws.add_mcp("agent-B", self._make_mcp("b-tool"))

        mcps_a = await self._ws.list_mcps("agent-A")
        mcps_b = await self._ws.list_mcps("agent-B")
        names_a = [m.name for m in mcps_a]
        names_b = [m.name for m in mcps_b]
        self.assertIn("a-tool", names_a)
        self.assertIn("b-tool", names_b)
