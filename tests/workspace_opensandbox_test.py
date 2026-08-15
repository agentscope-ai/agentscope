# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for OpenSandboxWorkspace.

The whole module is skipped when the ``OPENSANDBOX_DOMAIN`` environment
variable is not set, because every test requires a live OpenSandbox
service.
"""
import os
import sys
import types
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.workspace import OpenSandboxWorkspace


# ── OpenSandbox availability check ─────────────────────────────────

_DOMAIN = os.getenv("OPENSANDBOX_DOMAIN", "")
_API_KEY = os.getenv("OPENSANDBOX_API_KEY", "")
_SKIP_REASON = "OPENSANDBOX_DOMAIN environment variable is not set"


class TestOpenSandboxWorkspaceConfig(IsolatedAsyncioTestCase):
    """Test SDK option forwarding without a live OpenSandbox service."""

    async def test_connection_proxy_and_volumes_are_forwarded(self) -> None:
        """Workspace options reach the corresponding SDK calls."""
        create = AsyncMock(return_value=MagicMock(id="sandbox-id"))

        class ConnectionConfig:
            """Capture SDK connection arguments."""

            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        opensandbox = types.ModuleType("opensandbox")
        opensandbox.Sandbox = types.SimpleNamespace(create=create)
        config = types.ModuleType("opensandbox.config")
        connection = types.ModuleType("opensandbox.config.connection")
        connection.ConnectionConfig = ConnectionConfig

        modules = {
            "opensandbox": opensandbox,
            "opensandbox.config": config,
            "opensandbox.config.connection": connection,
        }
        volumes = [{"name": "workspace-data"}]
        with patch.dict(sys.modules, modules):
            workspace = OpenSandboxWorkspace(
                use_server_proxy=True,
                volumes=volumes,
            )
            connection_config = workspace._connection_config()
            await workspace._create_sandbox()

        self.assertTrue(connection_config.kwargs["use_server_proxy"])
        self.assertEqual(create.await_args.kwargs["volumes"], volumes)

    async def test_manager_forwards_proxy_and_volumes(self) -> None:
        """Manager-created workspaces receive both SDK options."""
        captured: dict[str, object] = {}

        class FakeWorkspace:
            """Capture manager workspace arguments."""

            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            async def initialize(self) -> None:
                """Stand in for workspace startup."""

        volumes = [{"name": "workspace-data"}]
        manager = OpenSandboxWorkspaceManager(
            use_server_proxy=True,
            volumes=volumes,
        )
        with patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace",
            FakeWorkspace,
        ):
            await manager._build_and_start(
                workspace_id="workspace-id",
                user_id="user-id",
                agent_id="agent-id",
            )

        self.assertTrue(captured["use_server_proxy"])
        self.assertEqual(captured["volumes"], volumes)


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
