# -*- coding: utf-8 -*-
"""Unit tests for BackgroundTaskManager."""
import asyncio
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app._manager import BackgroundTaskManager


class _FakeBus:
    """Minimal bus recording the registry calls the manager makes."""

    def __init__(self, fail_set: bool = False) -> None:
        self.fail_set = fail_set
        self.set_calls: list[tuple] = []
        self.del_calls: list[tuple] = []

    async def registry_set(
        self,
        key: str,
        entry_id: str,
        value: str,
        ttl_secs: int | None = None,
    ) -> None:
        """Record the write, failing when configured to."""
        self.set_calls.append((key, entry_id, value, ttl_secs))
        if self.fail_set:
            raise ConnectionError("redis down")

    async def registry_del(self, key: str, entry_id: str) -> bool:
        """Record the cleanup."""
        self.del_calls.append((key, entry_id))
        return True


class BackgroundTaskManagerTest(IsolatedAsyncioTestCase):
    """Registration and cleanup behaviour."""

    async def test_registry_failure_keeps_local_task_tracked(self) -> None:
        """A failed global-registry write must not leak or abort the task.

        The task is already in the local table when the registry write runs;
        it must still be removed when it finishes, which requires the done
        callback to be attached before that write.
        """
        bus = _FakeBus(fail_set=True)
        manager = BackgroundTaskManager(bus)  # type: ignore[arg-type]

        task = asyncio.create_task(asyncio.sleep(0))
        task_id = await manager.register_task(
            task,
            session_id="s",
            agent_id="a",
            user_id="u",
            tool_name="t",
        )

        self.assertIn(task_id, manager.tasks)

        await task
        await asyncio.sleep(0.05)

        self.assertNotIn(task_id, manager.tasks)
        self.assertEqual(len(bus.set_calls), 1)
        self.assertEqual([call[1] for call in bus.del_calls], [task_id])
