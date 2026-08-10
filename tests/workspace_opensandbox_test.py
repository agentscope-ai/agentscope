# -*- coding: utf-8 -*-
"""Test cases for OpenSandboxWorkspace.

The whole module is skipped when the ``OPENSANDBOX_DOMAIN`` environment
variable is not set, because every test requires a live OpenSandbox
service.
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
