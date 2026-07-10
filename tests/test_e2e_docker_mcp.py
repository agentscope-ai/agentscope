# -*- coding: utf-8 -*-
"""E2E test: per-agent MCP isolation via DockerWorkspace.

Requires: Docker running locally.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from agentscope.workspace import DockerWorkspace
from agentscope.mcp import MCPClient, StdioMCPConfig


# ── minimal MCP stdio server (runs inside the container) ───────────

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


def _docker_available() -> bool:
    """Return ``True`` iff the Docker daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


_DOCKER_OK = _docker_available()
_SKIP_REASON = "Docker daemon not available"


@unittest.skipUnless(_DOCKER_OK, _SKIP_REASON)
@unittest.skipIf(
    sys.platform == "win32",
    "Docker on Windows CI uses Windows container mode, "
    "Linux images unavailable",
)
class TestDockerPerAgentMCP(unittest.IsolatedAsyncioTestCase):
    """Per-agent MCP isolation tests for DockerWorkspace."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _make_mcp(name: str) -> MCPClient:
        """Build a real MCP client backed by a minimal stdio MCP server
        that runs inside the container via ``python3 -c``.
        """
        return MCPClient(
            name=name,
            is_stateful=True,
            mcp_config=StdioMCPConfig(
                command="python3",
                args=["-c", _MINIMAL_MCP_SERVER],
            ),
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
