# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for shared background task lifecycle and endpoints."""

import asyncio
import json
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import httpx

from agentscope.app import create_app
from agentscope.app._manager import (
    BackgroundTaskManager,
    CancelDispatcher,
    ChatRunRegistry,
    TaskStatus,
)
from agentscope.app._manager._background_task_manager import (
    _MAX_RETAINED_TASKS,
    BackgroundTask,
    BackgroundTaskRecord,
)
from agentscope.app.message_bus import (
    InMemoryMessageBus,
    MessageBus,
    MessageBusKeys,
    RedisMessageBus,
)
from agentscope.app.middleware._tool_offload_middleware import (
    _make_summary,
)

_MANAGER_MODULE = BackgroundTaskManager.__module__


class TestBackgroundTaskCompatibility(IsolatedAsyncioTestCase):
    """Preserve the task dataclass API that existed before this feature."""

    async def test_legacy_construction_and_dataclass_operations(self) -> None:
        """Old positional and keyword construction keep their field order."""
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        positional = BackgroundTask(task, "s1", "a1", "alice", "Tool", "id")
        keyword = BackgroundTask(
            asyncio_task=task,
            session_id="s1",
            agent_id="a1",
            user_id="alice",
            tool_name="Tool",
            id="id",
            started_at=positional.started_at,
        )
        expected = {
            "asyncio_task": task,
            "session_id": "s1",
            "agent_id": "a1",
            "user_id": "alice",
            "tool_name": "Tool",
            "id": "id",
            "summary": "",
            "started_at": positional.started_at,
            "completed_at": None,
            "status": TaskStatus.RUNNING,
            "error_summary": None,
        }
        self.assertTrue(is_dataclass(positional))
        self.assertEqual(
            [item.name for item in fields(BackgroundTask)],
            list(expected),
        )
        self.assertEqual(vars(positional), expected)
        self.assertEqual(positional, keyword)
        self.assertEqual(
            vars(replace(positional, tool_name="Other")),
            {**expected, "tool_name": "Other"},
        )
        self.assertEqual(vars(positional), expected)
        generated = BackgroundTask(task, "s1", "a1", "alice", "Tool")
        self.assertIsInstance(generated.id, str)
        self.assertNotEqual(generated.id, positional.id)

    async def test_shared_record_is_an_independent_snapshot(self) -> None:
        """Serialization excludes handles and cannot mutate the local task."""
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        local = BackgroundTask(
            task,
            "s1",
            "a1",
            "alice",
            "Tool",
            "id",
            summary="Tool(x=1)",
            started_at=123.0,
        )
        record = BackgroundTaskRecord.model_validate(local)
        expected = {
            "id": "id",
            "session_id": "s1",
            "agent_id": "a1",
            "user_id": "alice",
            "tool_name": "Tool",
            "summary": "Tool(x=1)",
            "started_at": 123.0,
            "completed_at": None,
            "status": "running",
            "error_summary": None,
        }
        self.assertEqual(json.loads(record.model_dump_json()), expected)
        local.status = TaskStatus.COMPLETED
        local.completed_at = 124.0
        self.assertEqual(json.loads(record.model_dump_json()), expected)
        record.summary = "Changed"
        self.assertEqual(local.summary, "Tool(x=1)")


class TestBackgroundTaskRecords(  # pylint: disable=too-many-public-methods
    IsolatedAsyncioTestCase,
):
    """Exercise two workers sharing task metadata and cancellation signals."""

    def _make_bus(self) -> MessageBus:
        """Provide the shared backend for both managers."""
        return InMemoryMessageBus()

    async def asyncSetUp(self) -> None:
        """Keep the bus alive until both managers have finished cleanup."""
        self.bus = await self.enterAsyncContext(self._make_bus())
        self.owner = await self.enterAsyncContext(
            BackgroundTaskManager(self.bus),
        )
        self.reader = await self.enterAsyncContext(
            BackgroundTaskManager(self.bus),
        )

    async def _register(
        self,
        manager: BackgroundTaskManager,
        user_id: str = "alice",
        session_id: str = "s1",
    ) -> tuple[asyncio.Event, asyncio.Task, str]:
        """Register a tool that finishes only when explicitly released."""
        gate = asyncio.Event()
        task = asyncio.create_task(gate.wait())
        task_id = await manager.register_task(
            task,
            session_id,
            "a1",
            user_id,
            "Tool",
            summary="Tool(x=1)",
        )
        return gate, task, task_id

    async def _settle(self, *tasks: asyncio.Task) -> None:
        """Wait for execution and all metadata writes without timing sleeps."""
        await asyncio.gather(*tasks, return_exceptions=True)
        for manager in (self.owner, self.reader):
            while manager._completion_tasks:
                await asyncio.gather(*manager._completion_tasks)
                # Done callbacks may still be queued after gather returns.
                await asyncio.sleep(0)

    async def _renew_once(self) -> None:
        """Run one heartbeat pass without waiting for the lease interval."""
        self.owner._renewal_task.cancel()
        await asyncio.gather(
            self.owner._renewal_task,
            return_exceptions=True,
        )
        with patch(
            f"{_MANAGER_MODULE}.asyncio",
            wraps=asyncio,
            sleep=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ):
            try:
                await self.owner._renew_running_records()
            except asyncio.CancelledError:
                pass

    async def test_settle_waits_for_pending_done_callbacks(self) -> None:
        """Completed tasks must be discarded before the helper returns."""
        completed = asyncio.create_task(asyncio.sleep(0))
        await completed
        self.owner._completion_tasks.add(completed)
        completed.add_done_callback(self.owner._completion_tasks.discard)

        await self._settle()

        self.assertEqual(self.owner._completion_tasks, set())
        self.assertEqual(self.reader._completion_tasks, set())

    async def test_shared_lifecycle_and_isolation(self) -> None:
        """Running and completed records are identical on either worker."""
        gate, task, task_id = await self._register(self.owner)
        expected = BackgroundTaskRecord.model_validate(
            self.owner.tasks[task_id],
        ).model_dump()
        record = await self.reader.get_task(task_id, user_id="alice")
        self.assertEqual(record.model_dump(), expected)
        self.assertEqual(
            [
                r.model_dump()
                for r in await self.reader.list_tasks(
                    user_id="alice",
                    session_id="s1",
                )
            ],
            [expected],
        )
        self.assertEqual(
            await self.reader.list_tasks(user_id="alice", session_id="s2"),
            [],
        )
        self.assertEqual(await self.reader.list_tasks(user_id="bob"), [])
        self.assertIsNone(
            await self.reader.get_task(task_id, user_id="bob"),
        )
        self.assertIsNone(
            await self.reader.get_task("missing", user_id="alice"),
        )

        gate.set()
        await self._settle(task)
        record = await self.reader.get_task(task_id, user_id="alice")
        self.assertIsNotNone(record.completed_at)
        expected.update(
            status=TaskStatus.COMPLETED,
            completed_at=record.completed_at,
        )
        self.assertEqual(record.model_dump(), expected)
        self.assertEqual(self.owner.tasks, {})
        self.assertEqual(
            await self.bus.registry_getall(MessageBusKeys.bg_tasks("s1")),
            {},
        )

    async def test_exception_records_failure(self) -> None:
        """An exception preserves its bounded error summary on any worker."""
        gate = asyncio.Event()

        async def fail() -> None:
            await gate.wait()
            raise ValueError("x" * 600)

        task = asyncio.create_task(fail())
        task_id = await self.owner.register_task(task, "s1", "a1", "alice")
        expected = BackgroundTaskRecord.model_validate(
            self.owner.tasks[task_id],
        ).model_dump()
        gate.set()
        await self._settle(task)
        record = await self.reader.get_task(task_id, user_id="alice")
        expected.update(
            status=TaskStatus.FAILED,
            error_summary="x" * 512,
            completed_at=record.completed_at,
        )
        self.assertIsNotNone(record.completed_at)
        self.assertEqual(record.model_dump(), expected)

    async def test_local_and_remote_toolstop(self) -> None:
        """Both cancellation paths retain status and remove live entries."""
        registry = await self.enterAsyncContext(ChatRunRegistry())
        await self.enterAsyncContext(
            CancelDispatcher(self.bus, registry, self.owner),
        )
        for caller in (self.owner, self.reader):
            with self.subTest(local=caller is self.owner):
                _, task, task_id = await self._register(self.owner)
                expected = BackgroundTaskRecord.model_validate(
                    self.owner.tasks[task_id],
                ).model_dump()
                stop = (await caller.list_tools("s1"))[0]
                await stop(task_id=task_id)
                await asyncio.wait_for(self._settle(task), timeout=5)
                record = await self.reader.get_task(
                    task_id,
                    user_id="alice",
                )
                self.assertTrue(task.cancelled())
                self.assertIsNotNone(record.completed_at)
                expected.update(
                    status=TaskStatus.CANCELLED,
                    completed_at=record.completed_at,
                )
                self.assertEqual(record.model_dump(), expected)
                self.assertEqual(self.owner.tasks, {})
                self.assertEqual(
                    await self.bus.registry_getall(
                        MessageBusKeys.bg_tasks("s1"),
                    ),
                    {},
                )
                self.assertFalse(self.owner.cancel_task(task_id))

    async def test_shutdown_waits_for_shared_cleanup(self) -> None:
        """Shutdown returns only after cancelled states and cleanup persist."""
        _, task, task_id = await self._register(self.owner)
        expected = BackgroundTaskRecord.model_validate(
            self.owner.tasks[task_id],
        ).model_dump()
        await self.owner.__aexit__()
        record = await self.reader.get_task(task_id, user_id="alice")
        expected.update(
            status=TaskStatus.CANCELLED,
            completed_at=record.completed_at,
        )
        self.assertTrue(task.cancelled())
        self.assertEqual(record.model_dump(), expected)
        self.assertEqual(self.owner.tasks, {})
        self.assertEqual(self.owner._completion_tasks, set())
        self.assertEqual(
            await self.bus.registry_getall(MessageBusKeys.bg_tasks("s1")),
            {},
        )

    async def test_retention_is_shared_and_scoped_per_user(self) -> None:
        """Concurrent completions evict oldest outcomes, never running work."""
        _, _, running_id = await self._register(self.owner)
        running = BackgroundTaskRecord.model_validate(
            self.owner.tasks[running_id],
        ).model_dump()
        bob_gate, bob_task, bob_id = await self._register(
            self.owner,
            user_id="bob",
        )
        work = []
        records = []
        for index in range(_MAX_RETAINED_TASKS + 5):
            manager = (self.owner, self.reader)[index % 2]
            gate, task, task_id = await self._register(manager)
            records.append(manager.tasks[task_id])
            work.append((gate, task))
        for gate, _ in work:
            gate.set()
        bob_gate.set()
        await self._settle(bob_task, *(task for _, task in work))

        retained = sorted(
            records,
            key=lambda record: (record.completed_at, record.id),
        )[-_MAX_RETAINED_TASKS:]
        expected = [running] + [
            BackgroundTaskRecord.model_validate(record).model_dump()
            for record in retained
        ]
        expected.sort(key=lambda record: (record["started_at"], record["id"]))
        self.assertEqual(
            [
                record.model_dump()
                for record in await self.reader.list_tasks(
                    user_id="alice",
                )
            ],
            expected,
        )
        bob_record = await self.reader.get_task(bob_id, user_id="bob")
        self.assertEqual(
            [
                record.model_dump()
                for record in await self.reader.list_tasks(
                    user_id="bob",
                )
            ],
            [bob_record.model_dump()],
        )
        self.assertEqual(bob_record.status, TaskStatus.COMPLETED)
        self.assertEqual(set(self.owner.tasks), {running_id})
        self.assertEqual(self.reader.tasks, {})

    async def test_failed_initial_write_still_cleans_up(self) -> None:
        """A partial registration cannot leave an untracked running task."""
        original_set = self.bus.registry_set

        async def fail_running_record(
            namespace: str,
            field: str,
            value: str,
            **kwargs: int,
        ) -> None:
            if (
                namespace == MessageBusKeys.bg_task_records("alice")
                and json.loads(value)["status"] == "running"
            ):
                raise ConnectionError("record write failed")
            await original_set(namespace, field, value, **kwargs)

        task = asyncio.create_task(asyncio.Event().wait())
        with patch.object(
            self.bus,
            "registry_set",
            side_effect=fail_running_record,
        ):
            with self.assertRaisesRegex(ConnectionError, "record write"):
                await self.owner.register_task(task, "s1", "a1", "alice")
            await self._settle(task)
        self.assertTrue(task.cancelled())
        self.assertEqual(self.owner.tasks, {})
        self.assertEqual(
            await self.bus.registry_getall(MessageBusKeys.bg_tasks("s1")),
            {},
        )

    async def test_initial_write_timeout_cleans_up_all_outcomes(self) -> None:
        """Either stalled initial write releases registration and handles."""
        original_set = self.bus.registry_set
        for namespace in (
            MessageBusKeys.bg_tasks("s1"),
            MessageBusKeys.bg_task_records("alice"),
        ):
            for completed in (False, True):
                with self.subTest(namespace=namespace, completed=completed):
                    started = asyncio.Event()
                    cancelled = asyncio.Event()
                    gate = asyncio.Event()
                    task = asyncio.create_task(gate.wait())

                    async def stall_initial_write(
                        ns: str,
                        field: str,
                        value: str,
                        *,
                        ttl_secs: int | None = None,
                    ) -> None:
                        # Each subtest finishes this write before advancing.
                        # pylint: disable=cell-var-from-loop
                        payload = json.loads(value)
                        if (
                            ns == namespace
                            and payload.get("status", "running") == "running"
                        ):
                            # The server may commit before the reply stalls.
                            await original_set(
                                ns,
                                field,
                                value,
                                ttl_secs=ttl_secs,
                            )
                            started.set()
                            try:
                                await asyncio.Event().wait()
                            finally:
                                cancelled.set()
                        await original_set(
                            ns,
                            field,
                            value,
                            ttl_secs=ttl_secs,
                        )

                    with (
                        patch.object(
                            self.bus,
                            "registry_set",
                            side_effect=stall_initial_write,
                        ),
                        patch(f"{_MANAGER_MODULE}._IO_TIMEOUT_SECS", 0.1),
                    ):
                        registration = asyncio.create_task(
                            self.owner.register_task(
                                task,
                                "s1",
                                "a1",
                                "alice",
                            ),
                        )
                        try:
                            await asyncio.wait_for(started.wait(), timeout=1)
                            (task_id,) = self.owner.tasks
                            expected = BackgroundTaskRecord.model_validate(
                                self.owner.tasks[task_id],
                            ).model_dump()
                            if completed:
                                gate.set()
                                await task
                            done, _ = await asyncio.wait(
                                {registration},
                                timeout=0.5,
                            )
                            self.assertEqual(done, {registration})
                            with self.assertRaises(TimeoutError):
                                await registration
                            self.assertTrue(cancelled.is_set())
                            await asyncio.wait_for(self._settle(task), 1)
                        finally:
                            registration.cancel()
                            await asyncio.gather(
                                registration,
                                return_exceptions=True,
                            )
                    self.assertEqual(self.owner.tasks, {})
                    self.assertEqual(task.cancelled(), not completed)
                    record = await self.reader.get_task(
                        task_id,
                        user_id="alice",
                    )
                    expected.update(
                        status=TaskStatus.COMPLETED
                        if completed
                        else TaskStatus.CANCELLED,
                        completed_at=record.completed_at,
                    )
                    self.assertEqual(record.model_dump(), expected)
                    self.assertEqual(
                        await self.bus.registry_getall(
                            MessageBusKeys.bg_tasks("s1"),
                        ),
                        {},
                    )

    async def test_failed_outcome_write_recovers_shared_status(self) -> None:
        """A failed write remains pending and is retried after recovery."""
        gate, task, task_id = await self._register(self.owner)
        expected = BackgroundTaskRecord.model_validate(
            self.owner.tasks[task_id],
        ).model_dump()
        failed = asyncio.Event()

        async def unavailable(_record: BackgroundTaskRecord) -> None:
            failed.set()
            raise ConnectionError("unavailable")

        with patch.object(
            self.owner,
            "_store_record",
            side_effect=unavailable,
        ):
            gate.set()
            await asyncio.wait_for(failed.wait(), timeout=1)
            self.assertEqual(self.owner.tasks, {})
            self.assertTrue(self.owner._completion_tasks)
            record = await self.reader.get_task(task_id, user_id="alice")
            self.assertEqual(record.model_dump(), expected)
        await asyncio.wait_for(self._settle(task), timeout=2)
        record = await self.reader.get_task(task_id, user_id="alice")
        self.assertIsNotNone(record.completed_at)
        expected.update(
            status=TaskStatus.COMPLETED,
            completed_at=record.completed_at,
        )
        self.assertEqual(record.model_dump(), expected)
        self.assertEqual(
            await self.bus.registry_getall(MessageBusKeys.bg_tasks("s1")),
            {},
        )
        self.assertEqual(self.owner._completion_tasks, set())

    async def test_failed_shutdown_record_expires_despite_new_tasks(
        self,
    ) -> None:
        """A lost outcome cannot remain running through user-hash renewal."""
        gate, task, task_id = await self._register(self.owner)
        namespace = MessageBusKeys.bg_task_records("alice")
        value = await self.bus.registry_get(namespace, task_id)
        expires_at = json.loads(value)["_expires_at"]
        failed = asyncio.Event()

        async def unavailable(_record: BackgroundTaskRecord) -> None:
            failed.set()
            raise ConnectionError("unavailable")

        with (
            patch.object(self.owner, "_store_record", side_effect=unavailable),
            patch(f"{_MANAGER_MODULE}._SHUTDOWN_TIMEOUT_SECS", 0.1),
        ):
            gate.set()
            await asyncio.wait_for(failed.wait(), timeout=1)
            await asyncio.wait_for(self.owner.__aexit__(), timeout=0.5)
        self.assertTrue(task.done())
        self.assertEqual(self.owner._completion_tasks, set())
        self.assertEqual(
            await self.bus.registry_get(namespace, task_id),
            value,
        )

        with patch(
            f"{_MANAGER_MODULE}.time.time",
            return_value=expires_at + 1,
        ):
            _, _, new_id = await self._register(self.reader)
            expected = BackgroundTaskRecord.model_validate(
                self.reader.tasks[new_id],
            ).model_dump()
            self.assertIsNone(
                await self.reader.get_task(task_id, user_id="alice"),
            )
            self.assertEqual(
                [
                    record.model_dump()
                    for record in await self.reader.list_tasks(
                        user_id="alice",
                    )
                ],
                [expected],
            )
            # Querying removes the expired field despite hash TTL renewal.
            self.assertIsNone(
                await self.bus.registry_get(namespace, task_id),
            )

    async def test_lists_remove_expired_records_as_user_activity_continues(
        self,
    ) -> None:
        """Repeated user activity cannot accumulate invisible running rows."""
        namespace = MessageBusKeys.bg_task_records("alice")
        expected = {}
        for batch in range(3):
            _, _, task_id = await self._register(self.owner)
            value = await self.bus.registry_get(namespace, task_id)
            expected[task_id] = value
            for index in range(5):
                stale_id = f"stale-{batch}-{index}"
                payload = json.loads(value)
                payload.update(id=stale_id, _expires_at=0)
                await self.bus.registry_set(
                    namespace,
                    stale_id,
                    json.dumps(payload),
                    ttl_secs=MessageBusKeys.BG_TASKS_TTL_SECS,
                )
            records = await self.reader.list_tasks(user_id="alice")
            self.assertEqual(
                {record.id: record.model_dump() for record in records},
                {
                    task_id: BackgroundTaskRecord.model_validate_json(
                        value,
                    ).model_dump()
                    for task_id, value in expected.items()
                },
            )
            self.assertEqual(
                await self.bus.registry_getall(namespace),
                expected,
            )

    async def test_completion_evicts_expired_running_and_terminal_records(
        self,
    ) -> None:
        """Finalization physically clears expired records of every status."""
        gate, task, task_id = await self._register(self.owner)
        namespace = MessageBusKeys.bg_task_records("alice")
        value = await self.bus.registry_get(namespace, task_id)
        for status in TaskStatus:
            stale_id = f"expired-{status.value}"
            payload = json.loads(value)
            payload.update(
                id=stale_id,
                status=status.value,
                _expires_at=0,
                completed_at=None if status == TaskStatus.RUNNING else 1.0,
            )
            await self.bus.registry_set(
                namespace,
                stale_id,
                json.dumps(payload),
            )
        gate.set()
        await self._settle(task)
        self.assertEqual(
            await self.bus.registry_getall(namespace),
            {task_id: await self.bus.registry_get(namespace, task_id)},
        )

    async def test_expiry_cleanup_preserves_concurrent_updates(self) -> None:
        """Queries and eviction cannot delete renewed or completed records."""
        namespace = MessageBusKeys.bg_task_records("alice")
        original_delete = self.bus.registry_del_if
        original_set = self.bus.registry_set
        for operation in ("list", "detail", "eviction"):
            for status in (TaskStatus.RUNNING, TaskStatus.COMPLETED):
                with self.subTest(operation=operation, status=status):
                    task_id = f"race-{operation}-{status.value}"
                    payload = {
                        "id": task_id,
                        "session_id": "s1",
                        "agent_id": "a1",
                        "user_id": "alice",
                        "tool_name": "Tool",
                        "summary": "Tool(x=1)",
                        "started_at": 0,
                        "completed_at": None,
                        "status": "running",
                        "error_summary": None,
                        "_expires_at": 0,
                    }
                    await original_set(namespace, task_id, json.dumps(payload))
                    payload.update(
                        status=status.value,
                        completed_at=1.0
                        if status == TaskStatus.COMPLETED
                        else None,
                        _expires_at=32503680000,
                    )
                    updated = json.dumps(payload)

                    async def concurrent_update(
                        ns: str,
                        field: str,
                        *,
                        expected: str,
                        new_value: str = updated,
                    ) -> bool:
                        await original_set(ns, field, new_value)
                        return await original_delete(
                            ns,
                            field,
                            expected=expected,
                        )

                    with patch.object(
                        self.bus,
                        "registry_del_if",
                        side_effect=concurrent_update,
                    ):
                        if operation == "list":
                            await self.reader.list_tasks(user_id="alice")
                        elif operation == "detail":
                            await self.reader.get_task(
                                task_id,
                                user_id="alice",
                            )
                        else:
                            await self.reader._evict_completed("alice")
                    self.assertEqual(
                        await self.bus.registry_get(namespace, task_id),
                        updated,
                    )
                    record = await self.reader.get_task(
                        task_id,
                        user_id="alice",
                    )
                    self.assertEqual(
                        record.model_dump(),
                        BackgroundTaskRecord.model_validate(
                            payload,
                        ).model_dump(),
                    )

    async def test_retention_lock_does_not_block_outcome_or_shutdown(
        self,
    ) -> None:
        """A held retention lock cannot delay state writes or exhaust exit."""
        gate, task, task_id = await self._register(self.owner)
        expected = BackgroundTaskRecord.model_validate(
            self.owner.tasks[task_id],
        ).model_dump()
        cleaned = asyncio.Event()
        original_del = self.bus.registry_del

        async def observe_cleanup(namespace: str, field: str) -> None:
            await original_del(namespace, field)
            if namespace == MessageBusKeys.bg_tasks("s1"):
                cleaned.set()

        async with self.bus.acquire_lock(
            MessageBusKeys.bg_task_records_lock("alice"),
            ttl_secs=600,
        ):
            with (
                patch.object(
                    self.bus,
                    "registry_del",
                    side_effect=observe_cleanup,
                ),
                patch(f"{_MANAGER_MODULE}._SHUTDOWN_TIMEOUT_SECS", 0.1),
                patch(f"{_MANAGER_MODULE}._IO_TIMEOUT_SECS", 0.02),
            ):
                gate.set()
                await asyncio.wait_for(cleaned.wait(), timeout=1)
                self.assertTrue(task.done())
                self.assertEqual(self.owner.tasks, {})
                record = await self.reader.get_task(task_id, user_id="alice")
                expected.update(
                    status=TaskStatus.COMPLETED,
                    completed_at=record.completed_at,
                )
                self.assertEqual(record.model_dump(), expected)
                await asyncio.wait_for(self.owner.__aexit__(), timeout=0.5)
                self.assertEqual(self.owner._completion_tasks, set())

    async def test_stalled_outcome_io_times_out_and_retries(self) -> None:
        """A nonresponsive write gets a deadline and can recover on retry."""
        gate, task, task_id = await self._register(self.owner)
        stalled = asyncio.Event()
        original_store = self.owner._store_record

        async def stall_once(record: BackgroundTaskRecord) -> None:
            if not stalled.is_set():
                stalled.set()
                await asyncio.Event().wait()
            await original_store(record)

        with (
            patch.object(self.owner, "_store_record", side_effect=stall_once),
            patch(f"{_MANAGER_MODULE}._RETRY_DELAY_SECS", 0),
        ):
            gate.set()
            await asyncio.wait_for(stalled.wait(), timeout=1)
            await asyncio.wait_for(self._settle(task), timeout=5)
        record = await self.reader.get_task(task_id, user_id="alice")
        self.assertEqual(record.status, TaskStatus.COMPLETED)
        self.assertEqual(
            await self.bus.registry_getall(MessageBusKeys.bg_tasks("s1")),
            {},
        )

    async def test_shutdown_cancels_stalled_cleanup_io(self) -> None:
        """Shutdown is bounded even if a registry operation never responds."""
        gate, task, task_id = await self._register(self.owner)
        blocked = asyncio.Event()
        cancelled = asyncio.Event()

        async def stall_delete(_namespace: str, _field: str) -> None:
            blocked.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with (
            patch.object(self.bus, "registry_del", side_effect=stall_delete),
            patch(f"{_MANAGER_MODULE}._SHUTDOWN_TIMEOUT_SECS", 0.1),
        ):
            gate.set()
            await asyncio.wait_for(blocked.wait(), timeout=1)
            self.assertTrue(task.done())
            await asyncio.wait_for(self.owner.__aexit__(), timeout=0.5)
            self.assertTrue(cancelled.is_set())
        self.assertEqual(self.owner._completion_tasks, set())
        record = await self.reader.get_task(task_id, user_id="alice")
        self.assertEqual(record.status, TaskStatus.COMPLETED)

    async def test_running_task_renews_its_own_lease(self) -> None:
        """Long-running tasks stay visible beyond their initial lease."""
        renewed = asyncio.Event()
        original_set_if = self.bus.registry_set_if

        async def observe_renewal(
            namespace: str,
            field: str,
            value: str,
            *,
            expected: str,
            ttl_secs: int | None = None,
        ) -> bool:
            result = await original_set_if(
                namespace,
                field,
                value,
                expected=expected,
                ttl_secs=ttl_secs,
            )
            renewed.set()
            return result

        with patch(f"{_MANAGER_MODULE}._RECORD_LEASE_SECS", 0.04):
            gate, task, task_id = await self._register(self.owner)
            value = await self.bus.registry_get(
                MessageBusKeys.bg_task_records("alice"),
                task_id,
            )
            future_time = json.loads(value)["_expires_at"] + 1
            with (
                patch(
                    f"{_MANAGER_MODULE}.time.time",
                    return_value=future_time,
                ),
                patch.object(
                    self.bus,
                    "registry_set_if",
                    side_effect=observe_renewal,
                ),
            ):
                await asyncio.wait_for(renewed.wait(), timeout=1)
                expected = BackgroundTaskRecord.model_validate(
                    self.owner.tasks[task_id],
                ).model_dump()
                record = await self.reader.get_task(task_id, user_id="alice")
                self.assertEqual(record.model_dump(), expected)
            gate.set()
            await self._settle(task)

    async def test_slow_io_keeps_all_running_tasks_visible(self) -> None:
        """Renewal latency must not accumulate across every running task."""
        original_get = self.bus.registry_get
        original_set_if = self.bus.registry_set_if

        async def delayed_get(namespace: str, field: str) -> str | None:
            await asyncio.sleep(0.04)
            return await original_get(namespace, field)

        async def delayed_set_if(
            namespace: str,
            field: str,
            value: str,
            *,
            expected: str | None,
            ttl_secs: int | None = None,
        ) -> bool:
            await asyncio.sleep(0.04)
            return await original_set_if(
                namespace,
                field,
                value,
                expected=expected,
                ttl_secs=ttl_secs,
            )

        with patch(f"{_MANAGER_MODULE}._RECORD_LEASE_SECS", 0.6):
            for _ in range(8):
                await self._register(self.owner)
            expected = {
                task.id: BackgroundTaskRecord.model_validate(task).model_dump()
                for task in self.owner.tasks.values()
            }
            with (
                patch.object(
                    self.bus,
                    "registry_get",
                    side_effect=delayed_get,
                ),
                patch.object(
                    self.bus,
                    "registry_set_if",
                    side_effect=delayed_set_if,
                ),
            ):
                await asyncio.sleep(0.65)
                for _ in range(4):
                    records = await self.reader.list_tasks(user_id="alice")
                    self.assertEqual(
                        {record.id: record.model_dump() for record in records},
                        expected,
                    )
                    self.assertEqual(
                        [
                            task.asyncio_task.done()
                            for task in self.owner.tasks.values()
                        ],
                        [False] * 8,
                    )
                    await asyncio.sleep(0.1)

    async def test_renewal_budget_preserves_fairness_and_cancels_io(
        self,
    ) -> None:
        """Slow tasks cannot starve the queue or leave I/O after shutdown."""
        original_get = self.bus.registry_get
        active: set[asyncio.Task] = set()
        seen: set[str] = set()
        peak = 0
        cancellations = 0
        reached_tail = asyncio.Event()
        start_renewal = asyncio.Event()
        original_renew = self.owner._renew_running_records

        async def renew_after_setup() -> None:
            """Keep registration latency out of the renewal queue order."""
            await start_renewal.wait()
            await original_renew()

        with (
            patch(f"{_MANAGER_MODULE}._RECORD_LEASE_SECS", 0.2),
            patch(f"{_MANAGER_MODULE}._RENEWAL_CONCURRENCY", 2),
            patch.object(
                self.owner,
                "_renew_running_records",
                side_effect=renew_after_setup,
            ),
        ):
            for _ in range(6):
                await self._register(self.owner)
            task_ids = list(self.owner.tasks)

            async def stalled_head(namespace: str, field: str) -> str | None:
                nonlocal peak, cancellations
                current = asyncio.current_task()
                assert current is not None
                active.add(current)
                peak = max(peak, len(active))
                try:
                    seen.add(field)
                    if seen == set(task_ids):
                        reached_tail.set()
                    if field in task_ids[:2]:
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            cancellations += 1
                            raise
                    return await original_get(namespace, field)
                finally:
                    active.remove(current)

            with patch.object(
                self.bus,
                "registry_get",
                side_effect=stalled_head,
            ):
                start_renewal.set()
                await asyncio.wait_for(reached_tail.wait(), timeout=0.5)
                self.assertEqual(peak, 2)
                self.assertGreaterEqual(cancellations, 2)
                self.assertEqual(seen, set(task_ids))
                await asyncio.wait_for(self.owner.__aexit__(), timeout=1)
            self.assertEqual(active, set())
            self.assertTrue(self.owner._renewal_task.done())

    async def test_registration_during_round_timeout_keeps_one_scheduler(
        self,
    ) -> None:
        """Temporary timeout cancellation must not start a second scheduler."""
        cancelling = asyncio.Event()
        allow_cleanup = asyncio.Event()
        resumed = asyncio.Event()
        original_renew = self.owner._renew_running_record

        async def delayed_cleanup(task: BackgroundTask) -> None:
            if not allow_cleanup.is_set():
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelling.set()
                    await allow_cleanup.wait()
                    raise
            await original_renew(task)
            resumed.set()

        with (
            patch(f"{_MANAGER_MODULE}._RECORD_LEASE_SECS", 0.2),
            patch(f"{_MANAGER_MODULE}._RENEWAL_CONCURRENCY", 2),
            patch.object(
                self.owner,
                "_renew_running_record",
                side_effect=delayed_cleanup,
            ),
        ):
            await self._register(self.owner)
            scheduler = self.owner._renewal_task
            try:
                await asyncio.wait_for(cancelling.wait(), timeout=2)
                self.assertGreater(scheduler.cancelling(), 0)
                self.assertFalse(scheduler.done())
                for _ in range(3):
                    await self._register(self.owner)
                self.assertIs(self.owner._renewal_task, scheduler)

                allow_cleanup.set()
                await asyncio.wait_for(resumed.wait(), timeout=2)
                self.assertIs(self.owner._renewal_task, scheduler)
                self.assertFalse(scheduler.done())
                await self.owner.__aexit__()
                self.assertTrue(scheduler.done())
                self.assertIs(self.owner._renewal_task, scheduler)
            finally:
                allow_cleanup.set()
                scheduler.cancel()
                await asyncio.gather(scheduler, return_exceptions=True)

    async def test_renewal_reuses_live_scheduler_and_restarts_after_exit(
        self,
    ) -> None:
        """New work reuses a live scheduler, including its idle interval."""
        sleeping = asyncio.Event()
        release_sleep = asyncio.Event()
        original_sleep = asyncio.sleep

        async def hold_first_sleep(delay: float) -> None:
            if not sleeping.is_set():
                sleeping.set()
                try:
                    await release_sleep.wait()
                except asyncio.CancelledError:
                    await release_sleep.wait()
                    raise
            else:
                await original_sleep(delay)

        with (
            patch(f"{_MANAGER_MODULE}._RECORD_LEASE_SECS", 0.2),
            patch(
                f"{_MANAGER_MODULE}.asyncio",
                wraps=asyncio,
                sleep=AsyncMock(side_effect=hold_first_sleep),
            ),
        ):
            gate, task, _ = await self._register(self.owner)
            scheduler = self.owner._renewal_task
            try:
                await asyncio.wait_for(sleeping.wait(), timeout=2)
                gate.set()
                await self._settle(task)
                self.assertEqual(scheduler.cancelling(), 0)
                self.assertFalse(scheduler.done())

                gate, task, _ = await self._register(self.owner)
                self.assertIs(self.owner._renewal_task, scheduler)
                gate.set()
                await self._settle(task)
                release_sleep.set()
                await asyncio.wait_for(asyncio.shield(scheduler), timeout=2)

                await self._register(self.owner)
                replacement = self.owner._renewal_task
                self.assertIsNot(replacement, scheduler)
                self.assertFalse(replacement.done())
                await self.owner.__aexit__()
                self.assertTrue(replacement.done())
            finally:
                release_sleep.set()
                scheduler.cancel()
                await asyncio.gather(scheduler, return_exceptions=True)

    async def test_heartbeat_restores_a_cleaned_running_record(self) -> None:
        """A live task reappears after its expired snapshot is deleted."""
        _, _, task_id = await self._register(self.owner)
        namespace = MessageBusKeys.bg_task_records("alice")
        value = await self.bus.registry_get(namespace, task_id)
        payload = json.loads(value)
        payload["_expires_at"] = 0
        await self.bus.registry_set(namespace, task_id, json.dumps(payload))
        self.assertEqual(await self.reader.list_tasks(user_id="alice"), [])
        self.assertIsNone(await self.bus.registry_get(namespace, task_id))

        await self._renew_once()

        expected = BackgroundTaskRecord.model_validate(
            self.owner.tasks[task_id],
        ).model_dump()
        record = await self.reader.get_task(task_id, user_id="alice")
        self.assertIsNotNone(record)
        self.assertEqual(record.model_dump(), expected)
        self.assertEqual(
            [
                r.model_dump()
                for r in await self.reader.list_tasks(
                    user_id="alice",
                )
            ],
            [expected],
        )

    async def test_heartbeat_restore_preserves_concurrent_outcome(
        self,
    ) -> None:
        """An outcome written after a missing read wins over recovery."""
        _, _, task_id = await self._register(self.owner)
        namespace = MessageBusKeys.bg_task_records("alice")
        await self.bus.registry_del(namespace, task_id)
        terminal = BackgroundTaskRecord.model_validate(
            self.owner.tasks[task_id],
        )
        terminal.status = TaskStatus.COMPLETED
        terminal.completed_at = terminal.started_at + 1
        original_set_if = self.bus.registry_set_if

        async def complete_before_write(
            namespace: str,
            field: str,
            value: str,
            *,
            expected: str | None,
            ttl_secs: int | None = None,
        ) -> bool:
            await self.owner._store_record(terminal)
            return await original_set_if(
                namespace,
                field,
                value,
                expected=expected,
                ttl_secs=ttl_secs,
            )

        with patch.object(
            self.bus,
            "registry_set_if",
            side_effect=complete_before_write,
        ) as restore:
            await self._renew_once()
        restore.assert_awaited_once()
        self.assertIsNone(restore.call_args.kwargs["expected"])
        record = await self.reader.get_task(task_id, user_id="alice")
        self.assertEqual(record.model_dump(), terminal.model_dump())

    async def test_heartbeat_skips_task_completed_during_read(self) -> None:
        """A missing read cannot revive a task that has already finished."""
        gate, task, task_id = await self._register(self.owner)
        namespace = MessageBusKeys.bg_task_records("alice")
        await self.bus.registry_del(namespace, task_id)
        original_get = self.bus.registry_get

        async def complete_during_read(
            namespace: str,
            field: str,
        ) -> str | None:
            value = await original_get(namespace, field)
            gate.set()
            await self._settle(task)
            return value

        with (
            patch.object(
                self.bus,
                "registry_get",
                side_effect=complete_during_read,
            ),
            patch.object(
                self.bus,
                "registry_set_if",
                wraps=self.bus.registry_set_if,
            ) as restore,
        ):
            await self._renew_once()
        restore.assert_not_awaited()
        record = await self.reader.get_task(task_id, user_id="alice")
        self.assertEqual(record.status, TaskStatus.COMPLETED)

    async def test_http_routes_read_another_workers_records(self) -> None:
        """The actual app exposes filtered lists, details and isolated 404s."""
        gate, task, task_id = await self._register(self.owner)
        record = await self.reader.get_task(task_id, user_id="alice")
        expected = {
            "task_id": task_id,
            "session_id": "s1",
            "agent_id": "a1",
            "tool_name": "Tool",
            "summary": "Tool(x=1)",
            "status": "running",
            "started_at": datetime.fromtimestamp(
                record.started_at,
                tz=timezone.utc,
            )
            .isoformat()
            .replace("+00:00", "Z"),
            "completed_at": None,
            "error_summary": None,
        }
        app = create_app(
            storage=MagicMock(),
            message_bus=self.bus,
            workspace_manager=MagicMock(),
        )
        app.state.background_task_manager = self.reader
        client = await self.enterAsyncContext(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ),
        )
        headers = {"X-User-ID": "alice"}
        for query, body in (
            ("", {"tasks": [expected], "total": 1}),
            ("?session_id=s1", {"tasks": [expected], "total": 1}),
            ("?session_id=s2", {"tasks": [], "total": 0}),
        ):
            response = await client.get(
                f"/background-tasks{query}",
                headers=headers,
            )
            self.assertEqual(
                (response.status_code, response.json()),
                (
                    200,
                    body,
                ),
            )
        response = await client.get(
            f"/background-tasks/{task_id}",
            headers=headers,
        )
        self.assertEqual(
            (response.status_code, response.json()),
            (
                200,
                expected,
            ),
        )
        for missing_id, user in ((task_id, "bob"), ("missing", "alice")):
            response = await client.get(
                f"/background-tasks/{missing_id}",
                headers={"X-User-ID": user},
            )
            self.assertEqual(
                (response.status_code, response.json()),
                (
                    404,
                    {"detail": f"Background task '{missing_id}' not found."},
                ),
            )
        response = await client.get("/background-tasks")
        self.assertEqual(response.status_code, 422)
        gate.set()
        await self._settle(task)
        record = await self.reader.get_task(task_id, user_id="alice")
        expected.update(
            status="completed",
            completed_at=datetime.fromtimestamp(
                record.completed_at,
                tz=timezone.utc,
            )
            .isoformat()
            .replace("+00:00", "Z"),
        )
        response = await client.get(
            f"/background-tasks/{task_id}",
            headers=headers,
        )
        self.assertEqual(
            (response.status_code, response.json()),
            (
                200,
                expected,
            ),
        )


class TestRedisBackgroundTaskRecords(TestBackgroundTaskRecords):
    """Run the same worker and lifecycle regressions through Redis APIs."""

    def _make_bus(self) -> MessageBus:
        """Use a fake Redis server without opening network connections."""
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(client.aclose)
        return RedisMessageBus(connection_pool=client.connection_pool)


class TestMakeSummary(IsolatedAsyncioTestCase):
    """Test the _make_summary helper function."""

    async def test_empty_input_returns_tool_name(self) -> None:
        """Empty or None input returns just the tool name."""
        self.assertEqual(_make_summary("Bash", None), "Bash")
        self.assertEqual(_make_summary("Bash", ""), "Bash")

    async def test_dict_input_includes_key_values(self) -> None:
        """JSON dict input produces key=value summary."""
        raw = json.dumps({"command": "ls -la", "timeout": 30})
        result = _make_summary("Bash", raw)
        self.assertIn("Bash(", result)
        self.assertIn("command=ls -la", result)
        self.assertIn("timeout=30", result)
        self.assertTrue(result.endswith(")"))

    async def test_respects_max_length(self) -> None:
        """Summary never exceeds _SUMMARY_MAX_LEN characters."""
        raw = json.dumps({"key": "x" * 200, "other": "y" * 200})
        result = _make_summary("VeryLongToolName", raw)
        self.assertLessEqual(len(result), 128)

    async def test_non_json_input_is_truncated(self) -> None:
        """Non-JSON input is included as-is with truncation."""
        raw = "a" * 100
        result = _make_summary("Tool", raw)
        self.assertIn("Tool(", result)
        self.assertIn("...", result)
        self.assertLessEqual(len(result), 128)

    async def test_non_json_short_input_no_ellipsis(self) -> None:
        """Short non-JSON input doesn't get a misleading ellipsis."""
        result = _make_summary("Tool", "hi")
        self.assertEqual(result, "Tool(hi)")

    async def test_empty_dict_returns_tool_name(self) -> None:
        """Empty JSON dict returns just the tool name."""
        result = _make_summary("Tool", "{}")
        self.assertEqual(result, "Tool")

    async def test_non_dict_json_returns_tool_name(self) -> None:
        """JSON that's not a dict returns just the tool name."""
        result = _make_summary("Tool", "[1, 2, 3]")
        self.assertEqual(result, "Tool")

    async def test_long_values_are_truncated(self) -> None:
        """Individual values longer than 40 chars are truncated."""
        raw = json.dumps({"file": "/" + "a" * 100})
        result = _make_summary("Read", raw)
        self.assertIn("...", result)
        self.assertLessEqual(len(result), 128)

    async def test_multiple_keys_included_within_budget(self) -> None:
        """Multiple short key-values are all included."""
        raw = json.dumps({"a": "1", "b": "2", "c": "3"})
        result = _make_summary("T", raw)
        self.assertIn("a=1", result)
        self.assertIn("b=2", result)
        self.assertIn("c=3", result)
