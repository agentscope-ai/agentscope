# -*- coding: utf-8 -*-
"""E2E test: per-agent MCP isolation via DockerWorkspace.

Requires: Docker running locally.
"""
import json
import os
import shutil
import tempfile
import unittest

from agentscope.workspace import DockerWorkspace
from agentscope.mcp import MCPClient, HttpMCPConfig


class TestDockerPerAgentMCP(unittest.IsolatedAsyncioTestCase):
    """Per-agent MCP isolation tests for DockerWorkspace."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_mcp(self, name: str) -> MCPClient:
        return MCPClient(
            name=name,
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="http://127.0.0.1:1/mcp"),
        )

    async def test_lazy_clone_from_default_mcps(
        self,
    ) -> None:
        """First list_mcps clones from default_mcps for each agent."""
        ws = DockerWorkspace(
            workspace_id="test-docker-clone",
            host_workdir=self.tmpdir,
            default_mcps=[self._make_mcp("default-fs")],
        )
        try:
            await ws.initialize()

            mcps_a = await ws.list_mcps("agent-A")
            self.assertEqual(len(mcps_a), 1)
            self.assertEqual(mcps_a[0].name, "default-fs")

            # Second access returns cached
            mcps_a2 = await ws.list_mcps("agent-A")
            self.assertEqual(len(mcps_a2), 1)

            # Agent B gets its own clone
            mcps_b = await ws.list_mcps("agent-B")
            self.assertEqual(len(mcps_b), 1)
            self.assertEqual(mcps_b[0].name, "default-fs")
        finally:
            await ws.close()

    async def test_add_remove_per_agent_isolation(self) -> None:
        """add_mcp / remove_mcp scoped to agent_id."""
        ws = DockerWorkspace(
            workspace_id="test-docker-addrm",
            host_workdir=self.tmpdir,
        )
        try:
            await ws.initialize()

            await ws.add_mcp("agent-A", self._make_mcp("extra"))
            mcps_a = await ws.list_mcps("agent-A")
            self.assertIn("extra", [m.name for m in mcps_a])

            # agent-B NOT affected
            mcps_b = await ws.list_mcps("agent-B")
            self.assertNotIn("extra", [m.name for m in mcps_b])

            # Remove from agent-A
            await ws.remove_mcp("agent-A", "extra")
            mcps_a = await ws.list_mcps("agent-A")
            self.assertNotIn("extra", [m.name for m in mcps_a])
        finally:
            await ws.close()

    async def test_duplicate_same_agent_raises(self) -> None:
        """Duplicate MCP name within same agent raises ValueError."""
        ws = DockerWorkspace(
            workspace_id="test-docker-dup",
            host_workdir=self.tmpdir,
        )
        try:
            await ws.initialize()
            await ws.add_mcp("agent-A", self._make_mcp("dup-me"))
            with self.assertRaises(ValueError):
                await ws.add_mcp("agent-A", self._make_mcp("dup-me"))
        finally:
            await ws.close()

    async def test_persistence_per_agent_format(self) -> None:
        """.mcp file uses {agent_id: [configs]} format."""
        ws = DockerWorkspace(
            workspace_id="test-docker-persist",
            host_workdir=self.tmpdir,
        )
        try:
            await ws.initialize()
            await ws.add_mcp("agent-A", self._make_mcp("a-tool"))
            await ws.add_mcp("agent-B", self._make_mcp("b-tool"))

            mcp_file = os.path.join(self.tmpdir, ".mcp")
            self.assertTrue(os.path.isfile(mcp_file))
            with open(mcp_file, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertIn("agent-A", saved)
            self.assertIn("agent-B", saved)
            self.assertEqual(len(saved["agent-A"]), 1)
            self.assertEqual(len(saved["agent-B"]), 1)
        finally:
            await ws.close()
