# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`DaytonaWorkspaceManager`."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import (
    DaytonaWorkspaceManager,
    IsolationPolicy,
)


class _FakeSandbox:
    """Daytona sandbox double recording a graceful stop."""

    def __init__(self) -> None:
        self.stopped = False

    async def stop(self, **_kwargs: object) -> None:
        """Record a graceful stop."""
        self.stopped = True


class _FakeDaytona:
    """Daytona client double recording deletes and client close."""

    def __init__(self) -> None:
        self.deleted: list[_FakeSandbox] = []
        self.closed = False
        self.delete_error: Exception | None = None

    async def delete(self, sandbox: _FakeSandbox) -> None:
        """Record a permanent delete, or raise the configured error."""
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(sandbox)

    async def close(self) -> None:
        """Record client shutdown."""
        self.closed = True


class _FakeWorkspace:
    """Workspace double whose ``close`` stops the sandbox, as Daytona's
    does; an unclaimed workspace must be deleted instead."""

    created: list["_FakeWorkspace"] = []
    initialize_error: Exception | None = None
    close_error: Exception | None = None
    daytona_delete_error: Exception | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.initialized = False
        self.closed = False
        self.close_calls = 0
        self._sandbox: _FakeSandbox | None = _FakeSandbox()
        self._sandbox_created = True
        self._daytona: _FakeDaytona | None = _FakeDaytona()
        self._daytona.delete_error = _FakeWorkspace.daytona_delete_error
        # Stable handles: disposal clears ``_sandbox`` on success.
        self.sandbox = self._sandbox
        self.daytona = self._daytona
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Mark initialized, or raise the configured error."""
        await asyncio.sleep(0)
        if _FakeWorkspace.initialize_error is not None:
            raise _FakeWorkspace.initialize_error
        self.initialized = True

    async def close(self) -> None:
        """Stop the sandbox and close the client, as the real close does."""
        self.close_calls += 1
        if _FakeWorkspace.close_error is not None:
            raise _FakeWorkspace.close_error
        if self._sandbox is not None:
            await self._sandbox.stop()
            self._sandbox = None
        if self._daytona is not None:
            await self._daytona.close()
            self._daytona = None
        self.closed = True


class TestDaytonaWorkspaceManager(IsolatedAsyncioTestCase):
    """Manager cache, metadata and TTL behavior."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
        _FakeWorkspace.initialize_error = None
        _FakeWorkspace.close_error = None
        _FakeWorkspace.daytona_delete_error = None
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_daytona_workspace_manager.DaytonaWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_get_workspace_forwards_config_and_metadata(self) -> None:
        """Manager forwards only the confirmed Daytona config surface."""
        manager = DaytonaWorkspaceManager(
            api_key="key",
            api_url="https://daytona.example/api",
            target="us",
            env={"A": "B"},
            sandbox_metadata={"team": "agents"},
            extra_pip=["x"],
            os_user="daytona",
            ttl=10,
            sweep_interval=1,
        )

        workspace = await manager.get_workspace("u1", "a1", "s1", "wid")

        self.assertIs(workspace, _FakeWorkspace.created[0])
        self.assertTrue(workspace.initialized)
        self.assertEqual(
            workspace.kwargs,
            {
                "workspace_id": "wid",
                "api_key": "key",
                "api_url": "https://daytona.example/api",
                "target": "us",
                "timeout_seconds": 300,
                "gateway_port": 5600,
                "env": {"A": "B"},
                "sandbox_metadata": {
                    "agentscope.user.id": "u1",
                    "agentscope.agent.id": "a1",
                    "team": "agents",
                },
                "extra_pip": ["x"],
                "default_mcps": [],
                "skill_paths": [],
                "os_user": "daytona",
            },
        )
        self.assertIn(workspace.workspace_id, manager._cache)

    async def test_get_workspace_uses_workspace_id_cache_key(self) -> None:
        """Same workspace id returns cached instance regardless of session."""
        manager = DaytonaWorkspaceManager()

        first = await manager.get_workspace("u", "a", "s1", "wid")
        second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertIs(first, second)
        self.assertEqual(len(_FakeWorkspace.created), 1)
        self.assertEqual(first.kwargs["workspace_id"], "wid")

    async def test_get_workspace_without_id_uses_isolation_policy(
        self,
    ) -> None:
        """``workspace_id=None`` follows the base manager API contract."""
        manager = DaytonaWorkspaceManager(
            isolation=IsolationPolicy.PER_USER,
        )

        first = await manager.get_workspace("u", "a1", "s1")
        second = await manager.get_workspace("u", "a2", "s2")

        self.assertIs(first, second)
        self.assertEqual(len(_FakeWorkspace.created), 1)
        self.assertEqual(
            first.kwargs["workspace_id"],
            await manager.assign_workspace_id(
                user_id="u",
                agent_id="a1",
                session_id="",
            ),
        )

    async def test_an_empty_workspace_id_is_not_a_binding(self) -> None:
        """Sessions persisted with ``workspace_id=""`` fall back to the
        isolation policy instead of pooling every user into one
        workspace keyed on the empty string."""
        manager = DaytonaWorkspaceManager()

        alice = await manager.get_workspace("alice", "a", "s", "")
        bob = await manager.get_workspace("bob", "a", "s", "")

        self.assertIsNot(alice, bob)
        self.assertListEqual(
            [alice.kwargs["workspace_id"], bob.kwargs["workspace_id"]],
            ["ca79105d522eba6f", "ecbe3dbe754c96ee"],
        )

    async def test_concurrent_get_workspace_creates_one_instance(self) -> None:
        """Concurrent requests for one id share the initialized workspace."""
        manager = DaytonaWorkspaceManager()

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
        manager = DaytonaWorkspaceManager()
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
        manager = DaytonaWorkspaceManager(ttl=10)
        workspace = await manager.get_workspace("u", "a", "s", "wid")
        manager._cache["wid"] = (workspace, 0.0)
        manager._safe_close = AsyncMock(wraps=manager._safe_close)

        await manager._sweep_once()

        self.assertNotIn("wid", manager._cache)
        self.assertTrue(workspace.closed)
        manager._safe_close.assert_awaited_once_with(workspace)

    async def test_context_manager_starts_sweeper_and_closes_all(self) -> None:
        """Async context starts the sweeper and closes cached workspaces."""
        manager = DaytonaWorkspaceManager(sweep_interval=60)
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

        await DaytonaWorkspaceManager._safe_close(  # type: ignore[arg-type]
            workspace,
        )

    async def test_failed_initialize_deletes_the_sandbox(self) -> None:
        """Delete (not stop) the sandbox and close the client."""
        _FakeWorkspace.initialize_error = RuntimeError("sandbox failed")
        manager = DaytonaWorkspaceManager()

        with self.assertRaisesRegex(RuntimeError, "sandbox failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(len(_FakeWorkspace.created), 1)
        workspace = _FakeWorkspace.created[0]
        self.assertEqual(workspace.close_calls, 1)
        self.assertListEqual(workspace.daytona.deleted, [workspace.sandbox])
        self.assertFalse(workspace.sandbox.stopped)
        self.assertTrue(workspace.daytona.closed)
        self.assertTrue(workspace.closed)
        self.assertEqual(manager._cache, {})

    async def test_failed_delete_falls_back_to_stop(self) -> None:
        """If the delete fails, the ordinary close path still runs."""
        _FakeWorkspace.initialize_error = RuntimeError("sandbox failed")
        _FakeWorkspace.daytona_delete_error = RuntimeError("delete failed")
        manager = DaytonaWorkspaceManager()

        with self.assertRaisesRegex(RuntimeError, "sandbox failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        workspace = _FakeWorkspace.created[0]
        self.assertListEqual(workspace.daytona.deleted, [])
        self.assertTrue(workspace.sandbox.stopped)
        self.assertTrue(workspace.daytona.closed)

    async def test_dispose_error_does_not_mask_initialize_error(self) -> None:
        """Cleanup failures are swallowed; the original error wins."""
        _FakeWorkspace.initialize_error = ValueError("gateway failed")
        _FakeWorkspace.close_error = RuntimeError("close failed")
        _FakeWorkspace.daytona_delete_error = RuntimeError("delete failed")
        manager = DaytonaWorkspaceManager()

        with self.assertRaisesRegex(ValueError, "gateway failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(_FakeWorkspace.created[0].close_calls, 1)
        self.assertEqual(manager._cache, {})

    async def test_retry_after_failure_recreates_the_sandbox(self) -> None:
        """Nothing is cached on failure, so the next call builds anew."""
        _FakeWorkspace.initialize_error = RuntimeError("sandbox failed")
        manager = DaytonaWorkspaceManager()
        with self.assertRaisesRegex(RuntimeError, "sandbox failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        _FakeWorkspace.initialize_error = None
        workspace = await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(len(_FakeWorkspace.created), 2)
        self.assertIs(manager._cache["ws-id"][0], workspace)
        self.assertEqual(_FakeWorkspace.created[0].close_calls, 1)
        self.assertEqual(_FakeWorkspace.created[1].close_calls, 0)


if __name__ == "__main__":
    unittest.main()
