# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :class:`CancelDispatcher` and its cancel subscriptions.

Verifies that on each incoming ``session_id`` the dispatcher:

- Cancels the chat-run task in :class:`ChatRunRegistry` when it owns one
  locally.
- Asks :class:`BackgroundTaskManager` to cancel local BG tasks for the
  same session.
- Silently does nothing for sessions whose state lives on other
  processes.

Also covers task-cancel and interrupt signals, including reconnects
after subscription failures.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase, mock

from agentscope.app._manager import (
    BackgroundTaskManager,
    CancelDispatcher,
    ChatRunRegistry,
)
from agentscope.app._manager._background_task_manager import ToolStop
from agentscope.app.message_bus import MessageBus, MessageBusKeys
from agentscope.message import ToolResultState


class _FakeBus(MessageBus):
    """In-memory bus with just enough behaviour for the dispatcher.

    Only the cancel-broadcast channel is exercised here; the other
    primitives are stubbed.
    """

    def __init__(self) -> None:
        self._channels: dict[str, asyncio.Queue] = {}
        self._locks: set[str] = set()
        self._registries: dict[str, dict[str, str]] = {}

    def _channel(self, key: str) -> asyncio.Queue:
        return self._channels.setdefault(key, asyncio.Queue())

    # Mode A — queue (unused)
    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        return "n/a"

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        return []

    async def queue_delete(self, key: str) -> None:
        return None

    # Mode C — log (unused)
    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        max_len: int | None = None,
        ttl_secs: int | None = None,
    ) -> str:
        return "n/a"

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        return []

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        return None

    # Mode D — pub/sub
    async def publish(self, key: str, payload: dict) -> None:
        await self._channel(key).put(payload)

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        if on_ready is not None:
            on_ready()
        while True:
            yield await self._channel(key).get()

    # Mode E — lock (unused)
    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        self._locks.add(key)
        try:
            yield
        finally:
            self._locks.discard(key)

    async def is_locked(self, key: str) -> bool:
        return key in self._locks

    async def try_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> bool:
        return True

    async def unlock(self, key: str) -> None:
        pass

    # Mode F — registry (in-memory dict)
    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        self._registries.setdefault(namespace, {})[field] = value

    async def registry_set_if(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        expected: str,
        ttl_secs: int | None = None,
    ) -> bool:
        raise NotImplementedError

    async def registry_pop(self, namespace: str, field: str) -> str | None:
        raise NotImplementedError

    async def registry_del(self, namespace: str, field: str) -> None:
        if namespace in self._registries:
            self._registries[namespace].pop(field, None)

    async def registry_exists(self, namespace: str, field: str) -> bool:
        return field in self._registries.get(namespace, {})

    async def registry_getall(self, namespace: str) -> dict[str, str]:
        return dict(self._registries.get(namespace, {}))

    async def registry_get(
        self,
        namespace: str,
        field: str,
    ) -> str | None:
        return self._registries.get(namespace, {}).get(field)

    async def registry_drop(self, namespace: str) -> None:
        self._registries.pop(namespace, None)


class _FlakyBus(_FakeBus):
    """Drop selected subscriptions once, then use the regular fake bus."""

    def __init__(
        self,
        fail_keys: set[str] | None = None,
        end_keys: set[str] | None = None,
        fail_before_ready: set[str] | None = None,
        fail_after_payload: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.fail_keys = fail_keys or set()
        self.end_keys = end_keys or set()
        self.fail_before_ready = fail_before_ready or set()
        self.fail_after_payload = fail_after_payload or set()
        self.attempts: dict[str, int] = {}
        self.resubscribed = {
            key: asyncio.Event()
            for key in self.fail_keys | self.end_keys | self.fail_after_payload
        }

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        attempt = self.attempts.get(key, 0) + 1
        self.attempts[key] = attempt
        if attempt == 1 and key in self.fail_keys | self.end_keys:
            if on_ready is not None and key not in self.fail_before_ready:
                on_ready()
            if key in self.fail_keys:
                raise ConnectionError("simulated Pub/Sub disconnect")
            return
        if key in self.fail_after_payload and attempt == 1:
            async for payload in super().subscribe(key, on_ready=on_ready):
                yield payload
                raise ConnectionError("simulated mid-stream disconnect")
            return
        if key in self.resubscribed:
            self.resubscribed[key].set()
        async for payload in super().subscribe(key, on_ready=on_ready):
            yield payload


async def _yield_a_few_times(ticks: int = 8) -> None:
    """Yield the event loop a few times so spawned tasks make progress."""
    for _ in range(ticks):
        await asyncio.sleep(0)


class _NeverEndingCoro:
    """Helper: yields a fresh coroutine that sleeps forever."""

    @staticmethod
    async def run() -> None:
        """The fake coroutine."""
        await asyncio.Event().wait()


class TestCancelDispatcher(IsolatedAsyncioTestCase):
    """Verifies the cross-process cancel fan-out."""

    async def test_session_cancel_reconnects_after_disconnect(self) -> None:
        """A later session signal still cancels a local run after drop."""
        key = MessageBusKeys.session_cancel_channel()
        bus = _FlakyBus(fail_keys={key})
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            await asyncio.wait_for(bus.resubscribed[key].wait(), timeout=2)
            chat_task = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="sess-recovered",
            )
            cancelled = asyncio.Event()
            chat_task.add_done_callback(lambda _: cancelled.set())
            await bus.publish(key, {"session_id": "sess-recovered"})
            await asyncio.wait_for(cancelled.wait(), timeout=1)
            self.assertTrue(chat_task.cancelled())
            self.assertEqual(bus.attempts[key], 2)

    async def test_task_cancel_reconnects_after_disconnect(self) -> None:
        """A later task signal still cancels a local background tool."""
        key = MessageBusKeys.task_cancel_channel()
        bus = _FlakyBus(fail_keys={key})
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            await asyncio.wait_for(bus.resubscribed[key].wait(), timeout=2)
            bg_task = asyncio.create_task(_NeverEndingCoro.run())
            task_id = await bg_manager.register_task(
                bg_task,
                session_id="sess-recovered",
                agent_id="agent",
                user_id="user",
            )
            cancelled = asyncio.Event()
            bg_task.add_done_callback(lambda _: cancelled.set())
            await bus.publish(key, {"task_id": task_id})
            await asyncio.wait_for(cancelled.wait(), timeout=1)
            self.assertTrue(bg_task.cancelled())
            self.assertEqual(bus.attempts[key], 2)

    async def test_interrupt_reconnects_after_disconnect(self) -> None:
        """A later interrupt still cancels its local chat run."""
        key = MessageBusKeys.session_interrupt_channel()
        bus = _FlakyBus(fail_keys={key})
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            await asyncio.wait_for(bus.resubscribed[key].wait(), timeout=2)
            chat_task = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="sess-recovered",
            )
            cancelled = asyncio.Event()
            chat_task.add_done_callback(lambda _: cancelled.set())
            await bus.publish(key, {"session_id": "sess-recovered"})
            await asyncio.wait_for(cancelled.wait(), timeout=1)
            self.assertTrue(chat_task.cancelled())
            self.assertEqual(bus.attempts[key], 2)

    async def test_subscribe_failure_before_ready_does_not_block_startup(
        self,
    ) -> None:
        """A failed initial subscribe is retried without hanging entry."""
        key = MessageBusKeys.session_cancel_channel()
        bus = _FlakyBus(fail_keys={key}, fail_before_ready={key})
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)
        dispatcher = CancelDispatcher(bus, registry, bg_manager)

        async with bg_manager, registry:
            async with asyncio.timeout(0.5):
                async with dispatcher:
                    self.assertEqual(bus.attempts[key], 1)

    async def test_subscription_normal_end_reconnects(self) -> None:
        """A closed stream is retried rather than ending the loop."""
        key = MessageBusKeys.session_cancel_channel()
        bus = _FlakyBus(end_keys={key})
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ) as dispatcher:
            await asyncio.wait_for(bus.resubscribed[key].wait(), timeout=2)
            self.assertEqual(bus.attempts[key], 2)
            self.assertFalse(dispatcher._session_task.done())

    async def test_mid_stream_disconnect_reconnects(self) -> None:
        """A channel that drops after a signal handles later signals."""
        key = MessageBusKeys.session_interrupt_channel()
        bus = _FlakyBus(fail_after_payload={key})
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            first = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="before-drop",
            )
            first_done = asyncio.Event()
            first.add_done_callback(lambda _: first_done.set())
            await bus.publish(key, {"session_id": "before-drop"})
            await asyncio.wait_for(first_done.wait(), timeout=1)
            self.assertTrue(first.cancelled())

            await asyncio.wait_for(bus.resubscribed[key].wait(), timeout=2)
            second = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="after-drop",
            )
            second_done = asyncio.Event()
            second.add_done_callback(lambda _: second_done.set())
            await bus.publish(key, {"session_id": "after-drop"})
            await asyncio.wait_for(second_done.wait(), timeout=1)
            self.assertTrue(second.cancelled())
            self.assertEqual(bus.attempts[key], 2)

    async def test_handler_failure_keeps_task_cancel_subscription(
        self,
    ) -> None:
        """A broken signal does not drop the healthy Pub/Sub stream."""
        key = MessageBusKeys.task_cancel_channel()
        bus = _FlakyBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            bg_task = asyncio.create_task(_NeverEndingCoro.run())
            task_id = await bg_manager.register_task(
                bg_task,
                session_id="sess-handler",
                agent_id="agent",
                user_id="user",
            )
            cancelled = asyncio.Event()
            bg_task.add_done_callback(lambda _: cancelled.set())
            first_handled = asyncio.Event()
            original_cancel = bg_manager.cancel_task

            def cancel_once_faulty(target_id: str) -> bool:
                if not first_handled.is_set():
                    first_handled.set()
                    raise RuntimeError("simulated handler failure")
                return original_cancel(target_id)

            with mock.patch.object(
                bg_manager,
                "cancel_task",
                side_effect=cancel_once_faulty,
            ):
                await bus.publish(key, {"task_id": "bad-signal"})
                await asyncio.wait_for(first_handled.wait(), timeout=1)
                await bus.publish(key, {"task_id": task_id})
                await asyncio.wait_for(cancelled.wait(), timeout=0.5)
                self.assertTrue(bg_task.cancelled())
                self.assertEqual(bus.attempts[key], 1)

    async def test_cancel_signal_cancels_local_chat_run(self) -> None:
        """Broadcast for a session whose chat run is registered locally
        cancels the registered asyncio task."""
        bus = _FakeBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            chat_task = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="sess-A",
            )
            await bus.session_publish_cancel("sess-A")

            # Wait until the cancel actually propagates.
            for _ in range(50):
                if chat_task.cancelled() or chat_task.done():
                    break
                await asyncio.sleep(0.01)

        self.assertTrue(chat_task.cancelled() or chat_task.done())

    async def test_cancel_signal_cancels_local_bg_tasks(self) -> None:
        """Broadcast for a session with locally-registered BG tasks
        cancels each of them; tasks for other sessions are untouched."""
        bus = _FakeBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            bg_task_a1 = asyncio.create_task(_NeverEndingCoro.run())
            bg_task_a2 = asyncio.create_task(_NeverEndingCoro.run())
            bg_task_b = asyncio.create_task(_NeverEndingCoro.run())

            await bg_manager.register_task(
                bg_task_a1,
                session_id="sess-A",
                agent_id="agent-A",
                user_id="u",
            )
            await bg_manager.register_task(
                bg_task_a2,
                session_id="sess-A",
                agent_id="agent-A",
                user_id="u",
            )
            await bg_manager.register_task(
                bg_task_b,
                session_id="sess-B",
                agent_id="agent-B",
                user_id="u",
            )

            await bus.session_publish_cancel("sess-A")

            for _ in range(50):
                if bg_task_a1.cancelled() and bg_task_a2.cancelled():
                    break
                await asyncio.sleep(0.01)

        self.assertTrue(bg_task_a1.cancelled() or bg_task_a1.done())
        self.assertTrue(bg_task_a2.cancelled() or bg_task_a2.done())
        # sess-B BG task is left running until shutdown cancels it.
        self.assertFalse(bg_task_b.cancelled())
        bg_task_b.cancel()

    async def test_cancel_signal_for_remote_session_is_noop(self) -> None:
        """Broadcast for a session held on another process is silently
        ignored — no exception, no spurious cancel."""
        bus = _FakeBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            # Register an unrelated chat run + unrelated BG task so we
            # can verify the unrelated work survives the broadcast.
            unrelated_chat = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="other",
            )
            unrelated_bg = asyncio.create_task(_NeverEndingCoro.run())
            await bg_manager.register_task(
                unrelated_bg,
                session_id="other",
                agent_id="agent",
                user_id="u",
            )

            await bus.session_publish_cancel("not-on-this-process")
            await _yield_a_few_times()

            self.assertFalse(unrelated_chat.cancelled())
            self.assertFalse(unrelated_bg.cancelled())

        # __aexit__ of ChatRunRegistry + BackgroundTaskManager cancels
        # the unrelated tasks on shutdown.

    async def test_cancel_fans_out_to_both_chat_and_bg_in_one_signal(
        self,
    ) -> None:
        """A single cancel broadcast cancels both the local chat run
        and the local BG task(s) for the session, not just one."""
        bus = _FakeBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            chat_task = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="sess-X",
            )
            bg_task = asyncio.create_task(_NeverEndingCoro.run())
            await bg_manager.register_task(
                bg_task,
                session_id="sess-X",
                agent_id="agent-X",
                user_id="u",
            )

            await bus.session_publish_cancel("sess-X")

            for _ in range(50):
                if chat_task.cancelled() and bg_task.cancelled():
                    break
                await asyncio.sleep(0.01)

        self.assertTrue(chat_task.cancelled() or chat_task.done())
        self.assertTrue(bg_task.cancelled() or bg_task.done())


class TestBackgroundTaskManagerCancelSessionTasks(IsolatedAsyncioTestCase):
    """Verifies :meth:`BackgroundTaskManager.cancel_session_tasks`."""

    async def test_cancels_only_matching_session(self) -> None:
        """Only tasks whose ``session_id`` matches are cancelled; the
        return value reports the local count."""
        bg_manager = BackgroundTaskManager(message_bus=_FakeBus())
        async with bg_manager:
            task_a = asyncio.create_task(_NeverEndingCoro.run())
            task_b = asyncio.create_task(_NeverEndingCoro.run())
            await bg_manager.register_task(
                task_a,
                session_id="match",
                agent_id="a",
                user_id="u",
            )
            await bg_manager.register_task(
                task_b,
                session_id="other",
                agent_id="b",
                user_id="u",
            )

            count = bg_manager.cancel_session_tasks("match")
            self.assertEqual(count, 1)

            for _ in range(50):
                if task_a.cancelled():
                    break
                await asyncio.sleep(0.01)

            self.assertTrue(task_a.cancelled())
            self.assertFalse(task_b.cancelled())

    async def test_no_matches_returns_zero(self) -> None:
        """A session with no locally-registered tasks returns 0 and
        does no work."""
        bg_manager = BackgroundTaskManager(message_bus=_FakeBus())
        async with bg_manager:
            task = asyncio.create_task(_NeverEndingCoro.run())
            await bg_manager.register_task(
                task,
                session_id="other",
                agent_id="a",
                user_id="u",
            )

            self.assertEqual(
                bg_manager.cancel_session_tasks("ghost"),
                0,
            )
            self.assertFalse(task.cancelled())


class TestToolStopRemoteCancel(IsolatedAsyncioTestCase):
    """Verifies the cross-worker cancel path of :class:`ToolStop`.

    A "worker A" registers a BG task in the shared bus registry, and a
    "worker B" — which has the task only in the global registry, not in
    its local cache — issues ``ToolStop``. The dispatcher on worker A
    must receive the broadcast and cancel the task locally.
    """

    async def test_remote_cancel_via_toolstop_broadcast(self) -> None:
        """ToolStop on a worker without the task publishes a task-level
        cancel; the owning worker's CancelDispatcher cancels the task."""
        bus = _FakeBus()

        # Worker A — owns the task and runs CancelDispatcher.
        bg_manager_owner = BackgroundTaskManager(message_bus=bus)
        registry_owner = ChatRunRegistry()

        # Worker B — only sees the task via the shared registry.
        bg_manager_caller = BackgroundTaskManager(message_bus=bus)

        async with bg_manager_owner, registry_owner, CancelDispatcher(
            message_bus=bus,
            registry=registry_owner,
            bg_manager=bg_manager_owner,
        ), bg_manager_caller:
            owned_task = asyncio.create_task(_NeverEndingCoro.run())
            task_id = await bg_manager_owner.register_task(
                owned_task,
                session_id="sess-shared",
                agent_id="agent",
                user_id="u",
                tool_name="LongRunningTool",
            )

            # Worker B's ToolStop: task_id is in the global registry but
            # not in worker B's local cache, so the remote-cancel path
            # is taken.
            tool_stop = ToolStop(
                background_tasks=bg_manager_caller.tasks,
                message_bus=bus,
                session_id="sess-shared",
            )
            chunk = await tool_stop(task_id=task_id)

            self.assertEqual(chunk.state, ToolResultState.SUCCESS)
            self.assertIn(
                "Cancel request sent",
                chunk.content[0].text,
            )

            for _ in range(50):
                if owned_task.cancelled() or owned_task.done():
                    break
                await asyncio.sleep(0.01)

            self.assertTrue(owned_task.cancelled() or owned_task.done())

    async def test_toolstop_does_not_cancel_other_session_locally(
        self,
    ) -> None:
        """A ToolStop instance bound to session A must not cancel a
        locally-tracked task that belongs to session B, even if the
        guessed task_id is correct."""
        bus = _FakeBus()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        async with bg_manager:
            victim_task = asyncio.create_task(_NeverEndingCoro.run())
            victim_task_id = await bg_manager.register_task(
                victim_task,
                session_id="sess-victim",
                agent_id="agent-v",
                user_id="u",
            )

            # ToolStop is bound to a *different* session; it should not
            # cancel ``victim_task`` directly. The shared registry is
            # also keyed by the bound session id, so the lookup misses
            # and we fall through to "not found".
            tool_stop = ToolStop(
                background_tasks=bg_manager.tasks,
                message_bus=bus,
                session_id="sess-attacker",
            )
            chunk = await tool_stop(task_id=victim_task_id)

            await _yield_a_few_times()

            self.assertEqual(chunk.state, ToolResultState.ERROR)
            self.assertIn(
                "TaskNotFoundError",
                chunk.content[0].text,
            )
            self.assertFalse(victim_task.cancelled())
            self.assertIn(victim_task_id, bg_manager.tasks)

            victim_task.cancel()
