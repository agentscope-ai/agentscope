# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`OpenSandboxWorkspaceManager`."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import (
    IsolationPolicy,
    OpenSandboxWorkspaceManager,
)


class _FakeWorkspace:
    """Workspace double used by manager tests.

    ``healthy`` mimics the server-side sandbox liveness that
    :meth:`OpenSandboxWorkspace.is_healthy` reports — flip it to
    ``False`` to simulate a sandbox that the OpenSandbox server killed
    after its ``timeout_seconds`` lifetime expired.
    """

    created: list["_FakeWorkspace"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.initialized = False
        self.closed = False
        self.healthy = True
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Mark initialized."""
        await asyncio.sleep(0)
        self.initialized = True

    async def close(self) -> None:
        """Mark closed."""
        self.closed = True

    async def is_healthy(self) -> bool:
        """Report the sandbox's simulated liveness."""
        return self.healthy


class TestOpenSandboxWorkspaceManager(IsolatedAsyncioTestCase):
    """Manager cache, liveness-check and TTL behavior."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_get_workspace_forwards_config_and_metadata(self) -> None:
        """Manager forwards only the confirmed OpenSandbox config surface."""
        manager = OpenSandboxWorkspaceManager(
            image="python:3.11-slim",
            api_key="key",
            domain="https://opensandbox.example",
            env={"A": "B"},
            sandbox_metadata={"team": "agents"},
            extra_pip=["x"],
            ttl=10,
            sweep_interval=1,
        )

        workspace = await manager.get_workspace("u1", "a1", "s1", "wid")

        self.assertIs(workspace, _FakeWorkspace.created[0])
        self.assertTrue(workspace.initialized)
        self.assertEqual(workspace.kwargs["workspace_id"], "wid")
        self.assertEqual(workspace.kwargs["image"], "python:3.11-slim")
        self.assertEqual(workspace.kwargs["api_key"], "key")
        self.assertEqual(
            workspace.kwargs["domain"],
            "https://opensandbox.example",
        )
        self.assertEqual(workspace.kwargs["env"], {"A": "B"})
        self.assertEqual(
            workspace.kwargs["sandbox_metadata"],
            {
                "agentscope.user.id": "u1",
                "agentscope.agent.id": "a1",
                "team": "agents",
            },
        )
        self.assertEqual(workspace.kwargs["extra_pip"], ["x"])
        self.assertIn(workspace.workspace_id, manager._cache)

    async def test_get_workspace_uses_workspace_id_cache_key(self) -> None:
        """Same workspace id returns cached instance regardless of session."""
        manager = OpenSandboxWorkspaceManager()

        first = await manager.get_workspace("u", "a", "s1", "wid")
        second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIs(first, second)
        self.assertEqual(len(_FakeWorkspace.created), 1)
        self.assertEqual(first.kwargs["workspace_id"], "wid")

    async def test_get_workspace_without_id_uses_isolation_policy(
        self,
    ) -> None:
        """``workspace_id=None`` follows the base manager API contract."""
        manager = OpenSandboxWorkspaceManager(
            isolation=IsolationPolicy.PER_USER,
        )

        first = await manager.get_workspace("u", "a1", "s1")
        second = await manager.get_workspace("u", "a2", "s2")

        self.assertIs(first, second)
        self.assertEqual(len(_FakeWorkspace.created), 1)
        self.assertEqual(
            first.kwargs["workspace_id"],
            manager.assign_workspace_id(
                user_id="u",
                agent_id="a1",
                session_id="",
            ),
        )

    async def test_concurrent_get_workspace_creates_one_instance(self) -> None:
        """Concurrent requests for one id share the initialized workspace."""
        manager = OpenSandboxWorkspaceManager()

        results = await asyncio.gather(
            *(
                manager.get_workspace("u", "a", f"s{i}", "wid-concurrent")
                for i in range(8)
            ),
        )

        self.assertEqual(len(_FakeWorkspace.created), 1)
        self.assertTrue(_FakeWorkspace.created[0].initialized)
        self.assertTrue(all(result is results[0] for result in results))
        self.assertIs(manager._cache["wid-concurrent"][0], results[0])

    async def test_close_and_close_all_release_cached_workspaces(self) -> None:
        """Explicit close operations evict and close workspaces."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s", "wid-1")
        second = await manager.get_workspace("u", "a", "s", "wid-2")

        await manager.close("wid-1")
        self.assertTrue(first.closed)
        self.assertNotIn("wid-1", manager._cache)

        await manager.close_all()
        self.assertTrue(second.closed)
        self.assertEqual(manager._cache, {})

    async def test_sweep_once_evicts_idle_workspaces(self) -> None:
        """The TTL sweeper closes expired cache entries."""
        manager = OpenSandboxWorkspaceManager(ttl=10)
        workspace = await manager.get_workspace("u", "a", "s", "wid")
        manager._cache["wid"] = (workspace, 0.0)
        manager._safe_close = AsyncMock(wraps=manager._safe_close)

        await manager._sweep_once()

        self.assertNotIn("wid", manager._cache)
        self.assertTrue(workspace.closed)
        manager._safe_close.assert_awaited_once_with(workspace)

    async def test_context_manager_starts_sweeper_and_closes_all(self) -> None:
        """Async context starts the sweeper and closes cached workspaces."""
        manager = OpenSandboxWorkspaceManager(sweep_interval=60)
        manager.close_all = AsyncMock(wraps=manager.close_all)

        async with manager as entered:
            sweep_task = manager._sweep_task

            self.assertIs(entered, manager)
            self.assertIsNotNone(sweep_task)
            self.assertFalse(sweep_task.done())

        self.assertIsNone(manager._sweep_task)
        self.assertTrue(sweep_task.done())
        manager.close_all.assert_awaited_once()

    async def test_safe_close_swallows_workspace_close_errors(self) -> None:
        """``_safe_close`` logs close errors without raising."""

        async def _raise_close() -> None:
            raise RuntimeError("close failed")

        workspace = SimpleNamespace(
            workspace_id="wid-error",
            close=_raise_close,
        )

        await OpenSandboxWorkspaceManager._safe_close(
            workspace,  # type: ignore[arg-type]
        )

    # ── liveness check (regression for #2202) ──────────────────────

    async def test_get_workspace_reuses_cached_healthy_workspace(
        self,
    ) -> None:
        """A cache hit whose sandbox is still alive is returned as-is,
        without rebuilding."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s", "wid")

        second = await manager.get_workspace("u", "a", "s", "wid")

        self.assertIs(first, second)
        self.assertEqual(len(_FakeWorkspace.created), 1)
        self.assertFalse(first.closed)

    async def test_get_workspace_evicts_and_rebuilds_dead_sandbox(
        self,
    ) -> None:
        """A cache hit whose sandbox was killed server-side (e.g. by the
        OpenSandbox ``timeout_seconds`` lifetime) is evicted and a fresh
        workspace is built and cached in its place — reproducing the
        permanent-404 zombie from #2202."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s", "wid")
        first.healthy = False

        second = await manager.get_workspace("u", "a", "s", "wid")

        self.assertIsNot(second, first)
        self.assertEqual(len(_FakeWorkspace.created), 2)
        self.assertTrue(first.closed)
        self.assertFalse(second.closed)
        self.assertIs(manager._cache["wid"][0], second)

    async def test_get_workspace_liveness_check_runs_outside_lock(
        self,
    ) -> None:
        """The remote ``is_healthy`` round-trip must not hold the
        manager lock, or one slow/dead sandbox would stall every other
        ``get_workspace`` call."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s", "wid-slow")

        probe_started = asyncio.Event()
        release_probe = asyncio.Event()

        async def _slow_is_healthy() -> bool:
            probe_started.set()
            await release_probe.wait()
            return True

        first.is_healthy = _slow_is_healthy

        task = asyncio.create_task(
            manager.get_workspace("u", "a", "s", "wid-slow"),
        )
        await asyncio.wait_for(probe_started.wait(), timeout=1.0)

        # The lock must be free while the probe is in flight — an
        # unrelated workspace_id should resolve immediately.
        other = await asyncio.wait_for(
            manager.get_workspace("u", "a", "s", "wid-other"),
            timeout=1.0,
        )
        self.assertIsNotNone(other)

        release_probe.set()
        result = await asyncio.wait_for(task, timeout=1.0)
        self.assertIs(result, first)


if __name__ == "__main__":
    unittest.main()
