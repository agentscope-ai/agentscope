# -*- coding: utf-8 -*-
"""Test cases for E2BWorkspace.

The whole module is skipped when the ``E2B_API_KEY`` environment variable is
not set, because every test requires a live E2B cloud sandbox.
"""
import os
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.workspace import E2BWorkspace


# ── E2B availability check ─────────────────────────────────────────

_E2B_API_KEY = os.getenv("E2B_API_KEY", "")
_SKIP_REASON = "E2B_API_KEY environment variable is not set"


# ── lifecycle tests ────────────────────────────────────────────────


@unittest.skipUnless(_E2B_API_KEY, _SKIP_REASON)
class TestE2BWorkspaceLifecycle(IsolatedAsyncioTestCase):
    """Test cases for E2BWorkspace lifecycle and MCP integration.

    Each test creates a real E2B cloud sandbox and tears it down
    (``pause``) afterwards.  The suite is skipped entirely when
    ``E2B_API_KEY`` is absent so that CI runs without E2B credentials
    are unaffected.
    """

    async def asyncSetUp(self) -> None:
        """No shared setup — each test manages its own workspace."""

    async def asyncTearDown(self) -> None:
        """No shared teardown — each test closes its own workspace."""

    async def test_initialize_and_list_mcps(self) -> None:
        """``initialize`` starts the sandbox and ``list_mcps`` enumerates MCPs.

        Verifies:
        1. The workspace initializes without raising.
        2. ``list_mcps`` returns at least the seeded MCP (browser-use).
        3. Each MCP exposes at least one tool via ``list_raw_tools``.
        4. ``close`` (sandbox pause) completes without raising.
        """
        workspace = E2BWorkspace(
            api_key=_E2B_API_KEY,
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
