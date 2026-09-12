# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""E2E test: per-scope MCP isolation via DockerWorkspace.

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
class TestDockerPerScopeMCP(unittest.IsolatedAsyncioTestCase):
    """Per-``(agent_id, session_id)`` MCP isolation for DockerWorkspace."""

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

    async def test_lazy_instantiation_from_default_mcps(self) -> None:
        """Each scope instantiates its own copy of ``default_mcps``."""
        ws = DockerWorkspace(
            workspace_id="test-docker-clone",
            host_workdir=self.tmpdir,
            default_mcps=[self._make_mcp("default-fs")],
        )
        try:
            await ws.initialize()

            # Nothing is connected until a scope asks.
            self.assertEqual(ws._mcp_instances, {})

            mcps_a = await ws.list_mcps(
                agent_id="agent-A",
                session_id="sess-1",
            )
            self.assertEqual([m.name for m in mcps_a], ["default-fs"])

            # Second access reuses the same instances.
            mcps_a2 = await ws.list_mcps(
                agent_id="agent-A",
                session_id="sess-1",
            )
            self.assertEqual([id(m) for m in mcps_a2], [id(m) for m in mcps_a])

            # A different session of the same agent gets its own.
            mcps_a_s2 = await ws.list_mcps(
                agent_id="agent-A",
                session_id="sess-2",
            )
            self.assertEqual([m.name for m in mcps_a_s2], ["default-fs"])
            self.assertIsNot(mcps_a_s2[0], mcps_a[0])

            # And so does a different agent.
            mcps_b = await ws.list_mcps(
                agent_id="agent-B",
                session_id="sess-1",
            )
            self.assertEqual([m.name for m in mcps_b], ["default-fs"])
            self.assertIsNot(mcps_b[0], mcps_a[0])
        finally:
            await ws.close()

    async def test_add_remove_per_scope_isolation(self) -> None:
        """``add_mcp`` / ``remove_mcp`` only touch the given scope."""
        ws = DockerWorkspace(
            workspace_id="test-docker-addrm",
            host_workdir=self.tmpdir,
        )
        try:
            await ws.initialize()

            await ws.add_mcp(
                self._make_mcp("extra"),
                agent_id="agent-A",
                session_id="sess-1",
            )
            mcps = await ws.list_mcps(agent_id="agent-A", session_id="sess-1")
            self.assertIn("extra", [m.name for m in mcps])

            # Neither another session of the same agent...
            other_session = await ws.list_mcps(
                agent_id="agent-A",
                session_id="sess-2",
            )
            self.assertNotIn("extra", [m.name for m in other_session])

            # ...nor another agent is affected.
            other_agent = await ws.list_mcps(
                agent_id="agent-B",
                session_id="sess-1",
            )
            self.assertNotIn("extra", [m.name for m in other_agent])

            await ws.remove_mcp(
                "extra",
                agent_id="agent-A",
                session_id="sess-1",
            )
            mcps = await ws.list_mcps(agent_id="agent-A", session_id="sess-1")
            self.assertNotIn("extra", [m.name for m in mcps])
        finally:
            await ws.close()

    async def test_duplicate_in_same_scope_raises(self) -> None:
        """A duplicate MCP name within one scope raises ``ValueError``."""
        ws = DockerWorkspace(
            workspace_id="test-docker-dup",
            host_workdir=self.tmpdir,
        )
        try:
            await ws.initialize()
            await ws.add_mcp(
                self._make_mcp("dup-me"),
                agent_id="agent-A",
                session_id="sess-1",
            )
            with self.assertRaises(ValueError):
                await ws.add_mcp(
                    self._make_mcp("dup-me"),
                    agent_id="agent-A",
                    session_id="sess-1",
                )

            # The same name in a different session is fine.
            await ws.add_mcp(
                self._make_mcp("dup-me"),
                agent_id="agent-A",
                session_id="sess-2",
            )
        finally:
            await ws.close()

    async def test_persistence_scoped_format(self) -> None:
        """``.mcp`` is written in the v2 nested agent/session format."""
        ws = DockerWorkspace(
            workspace_id="test-docker-persist",
            host_workdir=self.tmpdir,
        )
        try:
            await ws.initialize()
            mcp_file = os.path.join(self.tmpdir, ".mcp")

            # Untouched scopes leave no trace on disk.
            await ws.list_mcps(agent_id="agent-A", session_id="sess-1")
            self.assertFalse(os.path.exists(mcp_file))

            await ws.add_mcp(
                self._make_mcp("a-tool"),
                agent_id="agent-A",
                session_id="sess-1",
            )
            await ws.add_mcp(
                self._make_mcp("b-tool"),
                agent_id="agent-B",
                session_id="sess-2",
            )

            with open(mcp_file, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["version"], 2)
            self.assertEqual(
                [m["name"] for m in saved["mcps"]["agent-A"]["sess-1"]],
                ["a-tool"],
            )
            self.assertEqual(
                [m["name"] for m in saved["mcps"]["agent-B"]["sess-2"]],
                ["b-tool"],
            )

            # purge_session forgets the scope entirely.
            await ws.purge_session(agent_id="agent-A", session_id="sess-1")
            with open(mcp_file, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertNotIn("agent-A", saved["mcps"])
        finally:
            await ws.close()
