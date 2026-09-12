# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for OpenSandboxWorkspace.

Lease unit tests use mocked SDK connections. Only integration tests
require the ``OPENSANDBOX_DOMAIN`` environment variable.
"""
import asyncio
from datetime import timedelta
from importlib.util import find_spec
import os
from types import SimpleNamespace
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.workspace import OpenSandboxWorkspace
from agentscope.workspace._opensandbox._constants import DEFAULT_TIMEOUT


# ── OpenSandbox availability check ─────────────────────────────────

_DOMAIN = os.getenv("OPENSANDBOX_DOMAIN", "")
_API_KEY = os.getenv("OPENSANDBOX_API_KEY", "")
_SKIP_REASON = "OPENSANDBOX_DOMAIN environment variable is not set"


# ── lease unit tests ──────────────────────────────────────────────


@unittest.skipUnless(find_spec("opensandbox"), "opensandbox is not installed")
class TestOpenSandboxWorkspaceLease(IsolatedAsyncioTestCase):
    """Test demand-driven leases without an OpenSandbox server."""

    async def asyncSetUp(self) -> None:
        """Prepare a real workspace with an isolated SDK double."""
        self.workspace = OpenSandboxWorkspace(workspace_id="wid")
        self.sandbox = SimpleNamespace(
            id="sandbox-1",
            renew=AsyncMock(),
            get_info=AsyncMock(),
            pause=AsyncMock(),
            close=AsyncMock(),
        )
        self.workspace._sandbox = self.sandbox
        self.addAsyncCleanup(self.workspace.close)

    async def test_default_and_custom_lease_are_forwarded_on_creation(
        self,
    ) -> None:
        """Sandbox creation receives the default or explicit lease."""
        self.assertEqual(DEFAULT_TIMEOUT, 1800)
        self.assertEqual(self.workspace.timeout_seconds, 1800)
        for timeout in (1800, 90):
            with self.subTest(timeout=timeout):
                self.workspace.timeout_seconds = timeout
                with patch(
                    "opensandbox.Sandbox.create",
                    AsyncMock(return_value=self.sandbox),
                ) as create:
                    await self.workspace._create_sandbox()
                self.assertEqual(
                    create.call_args.kwargs["timeout"],
                    timedelta(seconds=timeout),
                )

    async def test_renewal_uses_configured_lease_without_precheck(
        self,
    ) -> None:
        """One access needs one renewal and no get_info request."""
        self.workspace.timeout_seconds = 90

        self.assertTrue(await self.workspace._renew_once())

        self.sandbox.renew.assert_awaited_once_with(timedelta(seconds=90))
        self.sandbox.get_info.assert_not_awaited()

    async def test_404_and_missing_handle_are_not_reusable(self) -> None:
        """Only confirmed disappearance requests replacement."""
        from opensandbox.exceptions import SandboxApiException

        self.sandbox.renew.side_effect = SandboxApiException(status_code=404)
        self.assertFalse(await self.workspace._renew_once())

        self.workspace._sandbox = None
        self.assertFalse(await self.workspace._renew_once())
        self.sandbox.renew.assert_awaited_once()

    async def test_new_sandbox_does_not_start_background_renewal(self) -> None:
        """A newly created sandbox already has its configured lease."""
        self.workspace._find_existing_sandbox = AsyncMock(return_value=None)
        self.workspace._create_sandbox = AsyncMock(return_value=self.sandbox)
        self.workspace._wait_until_running = AsyncMock()

        with patch(
            "agentscope.workspace._opensandbox."
            "_opensandbox_workspace.asyncio.create_task",
        ) as create_task:
            await self.workspace._provision_backend()
            create_task.assert_not_called()

        self.sandbox.renew.assert_not_awaited()
        self.assertIsNotNone(self.workspace._backend)

    async def test_connect_and_resume_renew_existing_lease(self) -> None:
        """Reattachment explicitly renews; SDK readiness timeouts do not."""
        self.workspace._wait_until_running = AsyncMock()
        for state, operation in (("Running", "connect"), ("Paused", "resume")):
            with self.subTest(state=state):
                self.sandbox.renew.reset_mock()
                self.workspace._find_existing_sandbox = AsyncMock(
                    return_value=SimpleNamespace(
                        id=self.sandbox.id,
                        status=SimpleNamespace(state=state),
                    ),
                )
                with patch(
                    f"opensandbox.Sandbox.{operation}",
                    AsyncMock(return_value=self.sandbox),
                ) as attach:
                    await self.workspace._provision_backend()

                attach.assert_awaited_once()
                self.sandbox.renew.assert_awaited_once_with(
                    timedelta(seconds=1800),
                )
                self.sandbox.get_info.assert_not_awaited()

    async def test_reattachment_404_cleans_partial_workspace(self) -> None:
        """A sandbox disappearing during reattachment fails with cleanup."""
        from opensandbox.exceptions import SandboxApiException

        self.workspace._find_existing_sandbox = AsyncMock(
            return_value=SimpleNamespace(id=self.sandbox.id),
        )
        self.workspace._attach_existing_sandbox = AsyncMock(
            return_value=self.sandbox,
        )
        self.sandbox.renew.side_effect = SandboxApiException(status_code=404)

        with self.assertRaisesRegex(RuntimeError, "disappeared"):
            await self.workspace.initialize()

        self.sandbox.close.assert_awaited_once()
        self.assertIsNone(self.workspace._sandbox)
        self.assertFalse(self.workspace.is_alive)

    async def test_initialize_failure_or_cancellation_cleans_resources(
        self,
    ) -> None:
        """Cleanup must preserve initialization errors and cancellation."""
        for error in (
            RuntimeError("bootstrap failed"),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error).__name__):
                self.sandbox.close.reset_mock()
                self.workspace._sandbox = self.sandbox
                gateway = SimpleNamespace(aclose=AsyncMock())
                self.workspace._gateway = gateway

                with patch(
                    "agentscope.workspace._sandboxed_base."
                    "SandboxedWorkspaceBase.initialize",
                    AsyncMock(side_effect=error),
                ):
                    with self.assertRaises(type(error)):
                        await self.workspace.initialize()

                gateway.aclose.assert_awaited_once()
                self.sandbox.close.assert_awaited_once()
                self.assertIsNone(self.workspace._gateway)
                self.assertIsNone(self.workspace._backend)
                self.assertIsNone(self.workspace._sandbox)
                self.assertFalse(self.workspace.is_alive)


# ── lifecycle integration tests ───────────────────────────────────


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
