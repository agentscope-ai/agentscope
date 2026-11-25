# -*- coding: utf-8 -*-
"""``ChatService.interrupt()`` must not spend a run on an idle session.

The interrupt endpoint promises an idempotent no-op for a session that
holds no run. Delivering that no-op through a ``resume`` run defeats the
promise: the empty run takes the session's registry slot, so a caller
that interrupts and immediately resends is rejected with "already has an
active chat run in this process" while it drains (#4231).

Only a parked session — one whose tail assistant message still has a
tool call awaiting a confirmation or an external result — has anything
to unwind, and only that session needs the resume run.
"""
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import AssistantMsg, ToolCallBlock
from agentscope.state import AgentState


class _Storage:
    """Serves one canned session to ``ChatService.interrupt()``."""

    def __init__(self, session: SimpleNamespace) -> None:
        """Keep the session record the service will read.

        Args:
            session (`SimpleNamespace`):
                The record ``get_session`` returns.
        """
        self._session = session

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SimpleNamespace:
        """Return the single canned session."""
        _ = user_id, agent_id, session_id
        return self._session


def _session(context: list[Any]) -> SimpleNamespace:
    """Build a session whose persisted context is *context*."""
    state = AgentState(context=context)
    if context:
        state.reply_id = context[0].id
    return SimpleNamespace(state=state)


def _service(
    context: list[Any],
) -> tuple[ChatService, InMemoryMessageBus]:
    """Build a ``ChatService`` over an in-memory bus and one session."""
    bus = InMemoryMessageBus()
    service = ChatService(
        storage=_Storage(_session(context)),
        workspace_manager=object(),
        scheduler_manager=object(),
        background_task_manager=object(),
        message_bus=bus,
        resource_access_service=object(),
    )
    return service, bus


async def _queue(bus: InMemoryMessageBus) -> list[tuple[str, dict]]:
    """Snapshot the shared wake-up queue."""
    return await bus.queue_read(MessageBusKeys.wakeup_queue(), max_count=10)


class ChatServiceInterruptTest(IsolatedAsyncioTestCase):
    """Interrupt enqueues a resume run only for a parked session."""

    async def test_idle_session_interrupt_enqueues_nothing(self) -> None:
        """An idle session has nothing to unwind, so no run is started."""
        service, bus = _service([])
        await service.interrupt("user-1", "session-1", "agent-1")
        self.assertEqual(await _queue(bus), [])

    async def test_settled_turn_interrupt_enqueues_nothing(self) -> None:
        """A finished turn (no pending tool call) is idle as well."""
        service, bus = _service(
            [AssistantMsg(name="agent", content="done")],
        )
        await service.interrupt("user-1", "session-1", "agent-1")
        self.assertEqual(await _queue(bus), [])

    async def test_parked_session_interrupt_enqueues_resume(self) -> None:
        """A tool call awaiting confirmation is still unwound by a run."""
        service, bus = _service(
            [
                AssistantMsg(
                    name="agent",
                    content=[
                        ToolCallBlock(
                            id="tc-1",
                            name="needs_confirmation",
                            input="{}",
                            state="asking",
                        ),
                    ],
                ),
            ],
        )
        await service.interrupt("user-1", "session-1", "agent-1")

        ((_, trigger),) = await _queue(bus)
        self.assertEqual(trigger["session_id"], "session-1")
        self.assertEqual(trigger["agent_id"], "agent-1")
        self.assertEqual(
            trigger["kind"],
            MessageBusKeys.WAKEUP_KIND_RESUME,
        )
        self.assertEqual(
            trigger["input"]["type"],
            "USER_INTERRUPT",
        )
