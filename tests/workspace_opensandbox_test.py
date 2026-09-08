# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for OpenSandboxWorkspace.

Most of this module is skipped when the ``OPENSANDBOX_DOMAIN``
environment variable is not set, because those tests require a live
OpenSandbox service. The ``is_healthy`` liveness-probe tests use a
sandbox double instead, so they always run.
"""
import os
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.workspace import OpenSandboxWorkspace


# ── OpenSandbox availability check ─────────────────────────────────

_DOMAIN = os.getenv("OPENSANDBOX_DOMAIN", "")
_API_KEY = os.getenv("OPENSANDBOX_API_KEY", "")
_SKIP_REASON = "OPENSANDBOX_DOMAIN environment variable is not set"


# ── liveness probe tests (regression for #2202) ────────────────────


class _FakeSandbox:
    """Sandbox double exposing only the ``is_healthy`` probe."""

    def __init__(self, healthy: bool = True, raises: bool = False) -> None:
        self._healthy = healthy
        self._raises = raises

    async def is_healthy(self) -> bool:
        """Report the simulated liveness, or raise if configured to."""
        if self._raises:
            raise RuntimeError("sandbox not found")
        return self._healthy


class TestOpenSandboxWorkspaceIsHealthy(IsolatedAsyncioTestCase):
    """``is_healthy`` before/after initialize and on probe failure."""

    async def test_is_healthy_false_before_initialize(self) -> None:
        """Without a bound sandbox, the workspace reports unhealthy."""
        workspace = OpenSandboxWorkspace()
        self.assertFalse(await workspace.is_healthy())

    async def test_is_healthy_reflects_sandbox_probe(self) -> None:
        """A live sandbox's probe result is forwarded verbatim."""
        workspace = OpenSandboxWorkspace()
        workspace._sandbox = _FakeSandbox(healthy=True)
        self.assertTrue(await workspace.is_healthy())

        workspace._sandbox = _FakeSandbox(healthy=False)
        self.assertFalse(await workspace.is_healthy())

    async def test_is_healthy_false_on_probe_error(self) -> None:
        """A probe that raises (e.g. sandbox 404) is treated as dead,
        not propagated."""
        workspace = OpenSandboxWorkspace()
        workspace._sandbox = _FakeSandbox(raises=True)
        self.assertFalse(await workspace.is_healthy())


# ── lifecycle tests ────────────────────────────────────────────────


@unittest.skipUnless(_DOMAIN, _SKIP_REASON)
class TestOpenSandboxWorkspaceLifecycle(IsolatedAsyncioTestCase):
    """Test cases for OpenSandboxWorkspace lifecycle and MCP integration.

    Each test creates a real OpenSandbox sandbox and tears it down
    (``pause``) afterward. The suite is skipped entirely when
    ``OPENSANDBOX_DOMAIN`` is absent so that CI runs without OpenSandbox
    access are unaffected.
    """

    async def test_initialize_and_list_mcps(self) -> None:
        """``initialize`` starts the sandbox and ``list_mcps`` enumerates MCPs.

        Verifies:
        1. The workspace initializes without raising.
        2. ``list_mcps`` returns at least the seeded MCP (browser-use).
        3. Each MCP exposes at least one tool via ``list_raw_tools``.
        4. ``close`` (sandbox pause) completes without raising.
        """
        workspace = OpenSandboxWorkspace(
            domain=_DOMAIN,
            api_key=_API_KEY,
            default_mcps=[
                MCPClient(
                    name="browser-use",
                    mcp_config=StdioMCPConfig(
                        command="npx",
                        args=["@playwright/mcp@latest"],
                    ),
                    is_stateful=True,
                ),
            ],
        )

        await workspace.initialize()

        mcps = await workspace.list_mcps()
        self.assertGreater(len(mcps), 0)

        for mcp in mcps:
            tools = await mcp.list_raw_tools()
            self.assertGreater(len(tools), 0)

        await workspace.close()


if __name__ == "__main__":
    unittest.main()
