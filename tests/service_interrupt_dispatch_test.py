# -*- coding: utf-8 -*-
# pylint: disable=missing-class-docstring,missing-function-docstring
"""Unit tests for :meth:`ChatService.interrupt` liveness dispatch.

Regression test for the agent-assembly interrupt race (issue #2320): a
chat run is spawned — and registered in ``ChatRunRegistry`` — *before*
:meth:`ChatService._run_impl` acquires the distributed session lock, so
``is_locked`` alone cannot tell "a reply is in flight here" during that
window. The fix makes :meth:`ChatService.interrupt` also consult the
per-process registry.

Covers the four dispatch outcomes:

- assembly window — live local task, lock not held → interrupt channel
  (cancel path; previously misrouted to a resume trigger)
- lock held (mid-reply / HITL-parked)             → interrupt channel
  (cancel path; unchanged)
- idle — no local task, lock not held             → resume trigger
  (unchanged)
- registry not wired (``None``), lock not held    → resume trigger
  (legacy fallback preserved)
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentscope.app._manager import (
    BackgroundTaskManager,
    CancelDispatcher,
    ChatRunRegistry,
)
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys


class _FakeBus:
    """Records ``publish`` calls and answers ``is_locked`` statically."""

    def __init__(self, locked: bool) -> None:
        self.locked = locked
        self.published: list[tuple[str, dict]] = []
        self.lock_checks: list[str] = []

    async def is_locked(self, key: str) -> bool:
        self.lock_checks.append(key)
        return self.locked

    async def publish(self, channel: str, payload: dict) -> None:
        self.published.append((channel, payload))


class _FakeStorage:
    """Returns a session carrying ``state.reply_id``."""

    def __init__(self) -> None:
        self.session = SimpleNamespace(
            state=SimpleNamespace(reply_id="reply-1"),
        )

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ):
        del user_id, agent_id, session_id
        return self.session


def _make_service(bus: _FakeBus, registry: ChatRunRegistry | None) -> ChatService:
    """Build a ChatService with only the deps ``interrupt`` touches wired.

    All other dependencies are ``None``; ``interrupt`` reads only
    ``_storage``, ``_message_bus`` and ``_chat_run_registry``.
    """
    return ChatService(
        storage=_FakeStorage(),
        workspace_manager=None,
        scheduler_manager=None,
        background_task_manager=None,
        message_bus=bus,
        resource_access_service=None,
        chat_run_registry=registry,
    )


class InterruptLivenessDispatchTest(unittest.IsolatedAsyncioTestCase):
    """Assert the cancel-vs-resume decision for each liveness state."""

    async def test_assembly_window_live_task_cancels(self) -> None:
        """A locally-live run with no lock yet must take the cancel path.

        This is the regression the fix addresses: before, ``is_locked``
        returned ``False`` during assembly and the interrupt was enqueued
        as a ``resume`` trigger the assembling run could not process.
        """
        bus = _FakeBus(locked=False)
        registry = ChatRunRegistry()
        gate = asyncio.Event()
        task = registry.spawn(gate.wait(), session_id="s")
        service = _make_service(bus, registry)
        try:
            with patch(
                "agentscope.app._service._chat.enqueue_run_trigger",
                new=AsyncMock(),
            ) as enqueue:
                await service.interrupt("u", "s", "a")
            self.assertEqual(
                bus.published,
                [
                    (
                        MessageBusKeys.session_interrupt_channel(),
                        {"session_id": "s"},
                    ),
                ],
            )
            enqueue.assert_not_awaited()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_lock_held_cancels(self) -> None:
        """A locked session (mid-reply / HITL-parked) still cancels."""
        bus = _FakeBus(locked=True)
        service = _make_service(bus, None)
        with patch(
            "agentscope.app._service._chat.enqueue_run_trigger",
            new=AsyncMock(),
        ) as enqueue:
            await service.interrupt("u", "s", "a")
        self.assertEqual(
            bus.published,
            [
                (
                    MessageBusKeys.session_interrupt_channel(),
                    {"session_id": "s"},
                ),
            ],
        )
        enqueue.assert_not_awaited()

    async def test_idle_empty_registry_resumes(self) -> None:
        """Idle session, registry present but empty → resume trigger."""
        bus = _FakeBus(locked=False)
        registry = ChatRunRegistry()
        service = _make_service(bus, registry)
        with patch(
            "agentscope.app._service._chat.enqueue_run_trigger",
            new=AsyncMock(),
        ) as enqueue:
            await service.interrupt("u", "s", "a")
        self.assertEqual(bus.published, [])
        enqueue.assert_awaited_once()
        self.assertEqual(
            enqueue.await_args.kwargs["kind"],
            MessageBusKeys.WAKEUP_KIND_RESUME,
        )

    async def test_no_registry_falls_back_to_resume(self) -> None:
        """Unwired registry (``None``) must preserve the legacy behaviour.

        Services built without a registry — tests, alternate wiring —
        fall back to the lock-only check, so nothing regresses for
        callers that have not opted into the registry.
        """
        bus = _FakeBus(locked=False)
        service = _make_service(bus, None)
        with patch(
            "agentscope.app._service._chat.enqueue_run_trigger",
            new=AsyncMock(),
        ) as enqueue:
            await service.interrupt("u", "s", "a")
        self.assertEqual(bus.published, [])
        enqueue.assert_awaited_once()
        self.assertEqual(
            enqueue.await_args.kwargs["kind"],
            MessageBusKeys.WAKEUP_KIND_RESUME,
        )
        self.assertEqual(
            enqueue.await_args.kwargs["inputs"].reply_id,
            "reply-1",
        )


    async def test_assembly_interrupt_reaches_cancel_dispatcher(
        self,
    ) -> None:
        """End-to-end: an assembly-window interrupt cancels the live task.

        The dispatch tests above stop at the ``publish`` boundary. This
        one wires the real pipeline — ``ChatService.interrupt`` →
        ``CancelDispatcher`` → ``ChatRunRegistry`` lookup →
        ``task.cancel()`` — against a task blocked *before* acquiring
        any lock (the assembly window), and asserts the task is actually
        cancelled. Guards the fix's premise ("the cancel machinery
        already exists and works for the assembly task") rather than
        asserting it.
        """
        bus = InMemoryMessageBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager(message_bus=bus)

        service = ChatService(
            storage=_FakeStorage(),
            workspace_manager=None,
            scheduler_manager=None,
            background_task_manager=None,
            message_bus=bus,
            resource_access_service=None,
            chat_run_registry=registry,
        )

        cancelled: list[bool] = []
        gate = asyncio.Event()

        async def _assembling() -> None:
            # Simulates a run mid-assembly: alive in the registry, no
            # session lock held, parked on an await.
            try:
                await gate.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        task = registry.spawn(_assembling(), session_id="s")

        async with bus:
            async with CancelDispatcher(
                message_bus=bus,
                registry=registry,
                bg_manager=bg_manager,
            ):
                await asyncio.sleep(0.05)  # let the dispatcher subscribe
                await service.interrupt("u", "s", "a")
                await asyncio.sleep(0.1)  # let the cancel propagate

        self.assertTrue(task.done(), "assembly-phase task must be cancelled")
        self.assertTrue(task.cancelled(), "task must end as cancelled")
        self.assertEqual(cancelled, [True], "CancelledError must be delivered")


if __name__ == "__main__":
    unittest.main()
