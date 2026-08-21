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


class _FakeWorkspace:
    """Workspace double used by manager tests."""

    created: list["_FakeWorkspace"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.initialized = False
        self.closed = False
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Mark initialized."""
        await asyncio.sleep(0)
        self.initialized = True

    async def close(self) -> None:
        """Mark closed."""
        self.closed = True


class TestDaytonaWorkspaceManager(IsolatedAsyncioTestCase):
    """Manager cache, metadata and TTL behavior."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
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
            manager.assign_workspace_id(
                user_id="u",
                agent_id="a1",
                session_id="",
            ),
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


if __name__ == "__main__":
    unittest.main()
