# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenSandbox workspace cache renewal and recovery."""

import asyncio
from datetime import timedelta
from importlib.util import find_spec
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager
from agentscope.workspace import OpenSandboxWorkspace


@unittest.skipUnless(find_spec("opensandbox"), "opensandbox is not installed")
class TestOpenSandboxWorkspaceManagerRecovery(IsolatedAsyncioTestCase):
    """Exercise real renewal and cleanup with mocked SDK connections."""

    async def asyncSetUp(self) -> None:
        """Replace sandbox provisioning with local workspace objects."""
        self.workspaces: list[OpenSandboxWorkspace] = []
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace",
            side_effect=self._make_workspace,
        )
        self.workspace_patch.start()
        self.addCleanup(self.workspace_patch.stop)

    def _make_workspace(self, **kwargs: Any) -> OpenSandboxWorkspace:
        """Construct a workspace without making any remote requests."""

        async def initialize() -> None:
            """Yield so concurrent requests can race during provisioning."""
            await asyncio.sleep(0)

        workspace = OpenSandboxWorkspace(**kwargs)
        workspace._sandbox = SimpleNamespace(
            id=f"sandbox-{len(self.workspaces) + 1}",
            renew=AsyncMock(),
            get_info=AsyncMock(),
            pause=AsyncMock(),
            close=AsyncMock(),
        )
        workspace._gateway = SimpleNamespace(aclose=AsyncMock())
        workspace.initialize = AsyncMock(side_effect=initialize)
        workspace.is_alive = True
        self.workspaces.append(workspace)
        return workspace

    async def test_cache_hit_renews_once_and_updates_access_time(self) -> None:
        """Reuse the same object with one renewal and no state pre-check."""
        for timeout in (None, 90):
            with self.subTest(timeout=timeout):
                manager = (
                    OpenSandboxWorkspaceManager()
                    if timeout is None
                    else OpenSandboxWorkspaceManager(timeout_seconds=timeout)
                )
                first = await manager.get_workspace("u", "a", "s1", "wid")
                sandbox = first._sandbox
                sandbox.renew.assert_not_awaited()
                manager._cache["wid"] = (first, 0.0)

                second = await manager.get_workspace("u", "a", "s2", "wid")

                self.assertIs(first, second)
                self.assertEqual(first.timeout_seconds, timeout or 1800)
                sandbox.renew.assert_awaited_once_with(
                    timedelta(seconds=timeout or 1800),
                )
                sandbox.get_info.assert_not_awaited()
                self.assertGreater(manager._cache["wid"][1], 0.0)

    async def test_404_recreates_workspace_and_closes_old_resources(
        self,
    ) -> None:
        """A missing sandbox is replaced under the same workspace id."""
        from opensandbox.exceptions import SandboxApiException

        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid")
        sandbox, gateway = first._sandbox, first._gateway
        sandbox.renew.side_effect = SandboxApiException(status_code=404)
        sandbox.pause.side_effect = SandboxApiException(status_code=404)

        with patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.logger.warning",
        ) as warning:
            second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIsNot(first, second)
        self.assertEqual(second.workspace_id, "wid")
        self.assertEqual(len(self.workspaces), 2)
        self.assertIs(manager._cache["wid"][0], second)
        warning.assert_any_call(
            "OpenSandbox workspace %r lost sandbox %r; recreating "
            "it. Ephemeral workspace data may have been lost.",
            "wid",
            sandbox.id,
        )
        gateway.aclose.assert_awaited_once()
        sandbox.close.assert_awaited_once()
        self.assertIsNone(first._sandbox)
        self.assertIsNone(first._backend)
        self.assertIsNone(first._gateway)
        self.assertFalse(first.is_alive)

    async def test_missing_local_handle_is_replaced(self) -> None:
        """A cached workspace without a sandbox handle is not reusable."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid")
        first._sandbox = None

        second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIsNot(first, second)
        self.assertEqual(len(self.workspaces), 2)
        self.assertFalse(first.is_alive)

    async def test_missing_workspace_id_is_resolved_before_caching(
        self,
    ) -> None:
        """An assigned id is used consistently for construction and reuse."""
        manager = OpenSandboxWorkspaceManager()
        manager.assign_workspace_id = AsyncMock(return_value="assigned-id")

        first = await manager.get_workspace("u", "a", "s1")
        second = await manager.get_workspace("u", "a", "s2", "assigned-id")

        self.assertIs(first, second)
        self.assertEqual(first.workspace_id, "assigned-id")
        self.assertIs(manager._cache["assigned-id"][0], first)
        manager.assign_workspace_id.assert_awaited_once_with(
            user_id="u",
            agent_id="a",
            session_id="",
        )

    async def test_renewal_errors_keep_cached_workspace(self) -> None:
        """API and network failures do not reject or replace a cache hit."""
        from opensandbox.exceptions import SandboxApiException

        for error in (
            SandboxApiException(status_code=503),
            SandboxApiException(status_code=401),
            TimeoutError("control plane unavailable"),
        ):
            with self.subTest(error=error):
                manager = OpenSandboxWorkspaceManager()
                first = await manager.get_workspace("u", "a", "s1", "wid")
                sandbox = first._sandbox
                sandbox.renew.side_effect = error
                manager._cache["wid"] = (first, 0.0)
                created = len(self.workspaces)

                with patch(
                    "agentscope.workspace._opensandbox."
                    "_opensandbox_workspace.logger.warning",
                ) as warning:
                    second = await manager.get_workspace("u", "a", "s2", "wid")

                self.assertIs(first, second)
                self.assertEqual(len(self.workspaces), created)
                self.assertGreater(manager._cache["wid"][1], 0.0)
                sandbox.close.assert_not_awaited()
                sandbox.get_info.assert_not_awaited()
                warning.assert_called_once()

    async def test_concurrent_expiration_creates_one_replacement(self) -> None:
        """Concurrent cache hits share exactly one recovery build."""
        from opensandbox.exceptions import SandboxApiException

        async def expired(*_: object) -> None:
            """Let other requests run before the remote 404 arrives."""
            await asyncio.sleep(0)
            raise SandboxApiException(status_code=404)

        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s0", "wid")
        sandbox = first._sandbox
        sandbox.renew.side_effect = expired

        results = await asyncio.gather(
            *(
                manager.get_workspace("u", "a", f"s{i}", "wid")
                for i in range(8)
            ),
        )

        self.assertEqual(len(self.workspaces), 2)
        self.assertTrue(all(item is results[0] for item in results))
        self.assertIs(manager._cache["wid"][0], results[0])
        sandbox.close.assert_awaited_once()
        sandbox.renew.assert_awaited_once()

    async def test_cancelled_renewal_propagates_without_eviction(self) -> None:
        """Cancellation is not mistaken for a failed lease renewal."""
        manager = OpenSandboxWorkspaceManager()
        workspace = await manager.get_workspace("u", "a", "s1", "wid")
        workspace._sandbox.renew.side_effect = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIs(manager._cache["wid"][0], workspace)
        self.assertFalse(manager._lock.locked())
        workspace._sandbox.close.assert_not_awaited()

    async def test_failed_replacement_is_not_cached(self) -> None:
        """Initialization failure propagates and leaves no stale entry."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid")
        first._sandbox = None
        with patch.object(
            manager,
            "_build_and_start",
            AsyncMock(side_effect=RuntimeError("bootstrap failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "bootstrap failed"):
                await manager.get_workspace("u", "a", "s2", "wid")
        self.assertNotIn("wid", manager._cache)

    async def test_idle_sweep_closes_without_renewing(self) -> None:
        """The sweeper evicts idle workspaces without renewing their lease."""
        manager = OpenSandboxWorkspaceManager()
        workspace = await manager.get_workspace("u", "a", "s1", "wid")
        sandbox = workspace._sandbox
        manager._cache["wid"] = (workspace, 0.0)

        await manager._sweep_once()

        self.assertNotIn("wid", manager._cache)
        sandbox.renew.assert_not_awaited()
        sandbox.pause.assert_awaited_once()
        sandbox.close.assert_awaited_once()

    async def test_close_all_preserves_existing_cleanup(self) -> None:
        """Closing all cached workspaces releases each connection."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid1")
        second = await manager.get_workspace("u", "a", "s2", "wid2")
        sandboxes = [first._sandbox, second._sandbox]

        await manager.close_all()

        self.assertFalse(manager._cache)
        for sandbox in sandboxes:
            sandbox.pause.assert_awaited_once()
            sandbox.close.assert_awaited_once()
            sandbox.renew.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
