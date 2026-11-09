# -*- coding: utf-8 -*-
"""Regression tests for background task registration and completion."""
import asyncio
import json
from unittest import IsolatedAsyncioTestCase

from utils import AnyValue

from agentscope.app._manager import BackgroundTaskManager
from agentscope.app._manager._background_task_manager import BackgroundTask
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys


class _RegistrationBus(InMemoryMessageBus):
    """Control write acknowledgement and record completion cleanup."""

    def __init__(self) -> None:
        """Initialize registry storage and deterministic I/O barriers."""
        super().__init__()
        self.write_started = asyncio.Event()
        self.allow_write = asyncio.Event()
        self.deleted = asyncio.Event()
        self.commit_before_wait = False
        self.write_error: ConnectionError | None = None
        self.delete_calls: list[tuple[str, str]] = []

    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        """Write before or after the acknowledgement barrier."""
        if self.commit_before_wait:
            await super().registry_set(
                namespace,
                field,
                value,
                ttl_secs=ttl_secs,
            )
        self.write_started.set()
        await self.allow_write.wait()
        if self.write_error is not None:
            raise self.write_error
        if not self.commit_before_wait:
            await super().registry_set(
                namespace,
                field,
                value,
                ttl_secs=ttl_secs,
            )

    async def registry_del(self, namespace: str, field: str) -> None:
        """Record cleanup and remove the registry entry."""
        self.delete_calls.append((namespace, field))
        self.deleted.set()
        await super().registry_del(namespace, field)


class BackgroundTaskManagerTest(IsolatedAsyncioTestCase):
    """Exercise registration faults with real asyncio task lifecycles."""

    async def asyncSetUp(self) -> None:
        """Create a manager and a task whose completion is controllable."""
        self.bus = _RegistrationBus()
        self.manager = BackgroundTaskManager(self.bus)
        self.finish = asyncio.Event()
        self.task = asyncio.create_task(self.finish.wait())
        self.registration: asyncio.Task[str] | None = None

    async def asyncTearDown(self) -> None:
        """Finish all test-owned tasks, including failed assertion paths."""
        tasks: list[asyncio.Task] = [self.task]
        if self.registration is not None:
            tasks.append(self.registration)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._checkpoint()

    @staticmethod
    async def _checkpoint() -> None:
        """Allow queued completion callbacks and cleanup tasks to run."""
        future = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(future.set_result, None)
        await future

    async def _start_registration(self) -> str:
        """Return the task id once registration reaches the write barrier."""
        self.registration = asyncio.create_task(
            self.manager.register_task(
                self.task,
                "session-a",
                "agent-a",
                "user-a",
                "slow_tool",
            ),
        )
        await asyncio.wait_for(self.bus.write_started.wait(), timeout=1)
        (task_id,) = self.manager.tasks
        self.assertEqual(
            self.manager.tasks,
            {
                task_id: BackgroundTask(
                    asyncio_task=self.task,
                    session_id="session-a",
                    agent_id="agent-a",
                    user_id="user-a",
                    tool_name="slow_tool",
                    id=task_id,
                ),
            },
        )
        return task_id

    async def _assert_cleaned(self, task_id: str) -> None:
        """Verify that completion removes both local and global entries."""
        await asyncio.wait_for(self.bus.deleted.wait(), timeout=1)
        self.assertEqual(self.manager.tasks, {})
        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.bg_tasks("session-a"),
            ),
            {},
        )
        self.assertEqual(
            self.bus.delete_calls,
            [(MessageBusKeys.bg_tasks("session-a"), task_id)],
        )

    async def _assert_registration_fault(
        self,
        *,
        committed: bool,
        cancelled: bool,
        complete_during_write: bool = False,
    ) -> None:
        """Verify faults preserve running tasks and clean up finished ones."""
        self.bus.commit_before_wait = committed
        error = ConnectionError("registry acknowledgement failed")
        self.bus.write_error = error
        task_id = await self._start_registration()
        assert self.registration is not None
        if complete_during_write:
            self.finish.set()
            await self.task
            await self._checkpoint()
            self.assertEqual(self.bus.delete_calls, [])
        if cancelled:
            self.registration.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.registration
        else:
            self.bus.allow_write.set()
            with self.assertRaises(ConnectionError) as raised:
                await self.registration
            self.assertIs(raised.exception, error)
        self.assertEqual(self.task.done(), complete_during_write)
        if not complete_during_write:
            self.assertEqual(list(self.manager.tasks), [task_id])
            self.assertEqual(self.bus.delete_calls, [])
        self.finish.set()
        self.assertTrue(await self.task)
        await self._assert_cleaned(task_id)

    async def test_error_before_write(self) -> None:
        """A failed write must not prevent eventual task cleanup."""
        await self._assert_registration_fault(committed=False, cancelled=False)

    async def test_error_after_write(self) -> None:
        """An accepted write with a failed acknowledgement is cleaned up."""
        await self._assert_registration_fault(committed=True, cancelled=False)

    async def test_cancel_before_write(self) -> None:
        """Cancelling registration preserves and eventually cleans the task."""
        await self._assert_registration_fault(committed=False, cancelled=True)

    async def test_cancel_after_write(self) -> None:
        """An accepted write is cleaned after registration cancellation."""
        await self._assert_registration_fault(committed=True, cancelled=True)

    async def test_completion_during_failed_write(self) -> None:
        """A task finished before a write error still triggers cleanup."""
        await self._assert_registration_fault(
            committed=True,
            cancelled=False,
            complete_during_write=True,
        )

    async def test_successful_registration(self) -> None:
        """Successful registration retains metadata until task completion."""
        task_id = await self._start_registration()
        assert self.registration is not None
        self.bus.allow_write.set()
        self.assertEqual(await self.registration, task_id)
        self.assertEqual(
            {
                key: json.loads(value)
                for key, value in (
                    await self.bus.registry_getall(
                        MessageBusKeys.bg_tasks("session-a"),
                    )
                ).items()
            },
            {
                task_id: {
                    "tool_name": "slow_tool",
                    "agent_id": "agent-a",
                    "started_at": AnyValue(),
                },
            },
        )
        self.assertEqual(self.bus.delete_calls, [])
        self.finish.set()
        await self.task
        await self._assert_cleaned(task_id)

    async def test_completion_during_write(self) -> None:
        """Cleanup waits for a pending write, preventing delete-before-set."""
        task_id = await self._start_registration()
        assert self.registration is not None
        self.finish.set()
        await self.task
        await self._checkpoint()
        self.assertEqual(self.bus.delete_calls, [])
        self.assertEqual(list(self.manager.tasks), [task_id])
        self.bus.allow_write.set()
        self.assertEqual(await self.registration, task_id)
        await self._assert_cleaned(task_id)

    async def test_already_completed_task(self) -> None:
        """Registering an already-finished task still cleans both stores."""
        self.finish.set()
        await self.task
        self.bus.allow_write.set()
        task_id = await self.manager.register_task(
            self.task,
            "session-a",
            "agent-a",
            "user-a",
        )
        await self._assert_cleaned(task_id)
