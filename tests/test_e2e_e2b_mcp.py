# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""E2E test: per-scope MCP isolation via E2BWorkspace.

Requires: ``E2B_API_KEY`` environment variable.
"""
import os
import unittest

from agentscope.workspace import E2BWorkspace
from agentscope.mcp import MCPClient, StdioMCPConfig


# ── minimal MCP stdio server (runs inside the sandbox) ─────────────

_MINIMAL_MCP_SERVER = """\
import json, sys

def _send(data):
    sys.stdout.write(json.dumps(data) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    req = json.loads(line)
    mid = req.get("id")
    method = req.get("method", "")
    if method == "initialize":
        _send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "test-mcp", "version": "0.1.0"},
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": []}})
"""

_E2B_API_KEY = os.getenv("E2B_API_KEY", "")
_SKIP_REASON = "E2B_API_KEY environment variable is not set"


@unittest.skipUnless(_E2B_API_KEY, _SKIP_REASON)
class TestE2BPerScopeMCP(unittest.IsolatedAsyncioTestCase):
    """Per-``(agent_id, session_id)`` MCP isolation for E2BWorkspace."""

    @staticmethod
    def _make_mcp(name: str) -> MCPClient:
        """Build a real MCP client backed by a minimal stdio MCP server
        that runs inside the sandbox via ``python3 -c``.
        """
        return MCPClient(
            name=name,
            is_stateful=True,
            mcp_config=StdioMCPConfig(
                command="python3",
                args=["-c", _MINIMAL_MCP_SERVER],
            ),
        )

    async def asyncSetUp(self) -> None:
        self._ws = E2BWorkspace(
            api_key=_E2B_API_KEY,
            default_mcps=[self._make_mcp("default-fs")],
        )
        await self._ws.initialize()

    async def asyncTearDown(self) -> None:
        await self._ws.close()

    async def test_lazy_instantiation_from_default_mcps(self) -> None:
        """Each scope instantiates its own copy of ``default_mcps``."""
        self.assertEqual(self._ws._mcp_instances, {})

        mcps_a = await self._ws.list_mcps(
            agent_id="agent-A",
            session_id="sess-1",
        )
        self.assertEqual([m.name for m in mcps_a], ["default-fs"])

        mcps_a2 = await self._ws.list_mcps(
            agent_id="agent-A",
            session_id="sess-1",
        )
        self.assertEqual([id(m) for m in mcps_a2], [id(m) for m in mcps_a])

        mcps_a_s2 = await self._ws.list_mcps(
            agent_id="agent-A",
            session_id="sess-2",
        )
        self.assertEqual([m.name for m in mcps_a_s2], ["default-fs"])
        self.assertIsNot(mcps_a_s2[0], mcps_a[0])

        mcps_b = await self._ws.list_mcps(
            agent_id="agent-B",
            session_id="sess-1",
        )
        self.assertEqual([m.name for m in mcps_b], ["default-fs"])
        self.assertIsNot(mcps_b[0], mcps_a[0])

    async def test_add_remove_per_scope_isolation(self) -> None:
        """``add_mcp`` / ``remove_mcp`` only touch the given scope."""
        await self._ws.add_mcp(
            self._make_mcp("extra"),
            agent_id="agent-A",
            session_id="sess-1",
        )
        mcps = await self._ws.list_mcps(
            agent_id="agent-A",
            session_id="sess-1",
        )
        self.assertIn("extra", [m.name for m in mcps])

        other_session = await self._ws.list_mcps(
            agent_id="agent-A",
            session_id="sess-2",
        )
        self.assertNotIn("extra", [m.name for m in other_session])

        other_agent = await self._ws.list_mcps(
            agent_id="agent-B",
            session_id="sess-1",
        )
        self.assertNotIn("extra", [m.name for m in other_agent])

        await self._ws.remove_mcp(
            "extra",
            agent_id="agent-A",
            session_id="sess-1",
        )
        mcps = await self._ws.list_mcps(
            agent_id="agent-A",
            session_id="sess-1",
        )
        self.assertNotIn("extra", [m.name for m in mcps])

    async def test_duplicate_in_same_scope_raises(self) -> None:
        """A duplicate MCP name within one scope raises ``ValueError``."""
        await self._ws.add_mcp(
            self._make_mcp("dup-me"),
            agent_id="agent-A",
            session_id="sess-1",
        )
        with self.assertRaises(ValueError):
            await self._ws.add_mcp(
                self._make_mcp("dup-me"),
                agent_id="agent-A",
                session_id="sess-1",
            )

        # The same name in a different session is fine.
        await self._ws.add_mcp(
            self._make_mcp("dup-me"),
            agent_id="agent-A",
            session_id="sess-2",
        )

    async def test_purge_session_drops_the_scope(self) -> None:
        """``purge_session`` forgets a scope; defaults come back."""
        await self._ws.add_mcp(
            self._make_mcp("a-tool"),
            agent_id="agent-A",
            session_id="sess-1",
        )
        mcps = await self._ws.list_mcps(
            agent_id="agent-A",
            session_id="sess-1",
        )
        self.assertIn("a-tool", [m.name for m in mcps])

        await self._ws.purge_session(agent_id="agent-A", session_id="sess-1")
        mcps = await self._ws.list_mcps(
            agent_id="agent-A",
            session_id="sess-1",
        )
        self.assertEqual([m.name for m in mcps], ["default-fs"])
