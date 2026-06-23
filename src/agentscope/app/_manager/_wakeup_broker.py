# -*- coding: utf-8 -*-
"""Composition helper for the wake-up channel.

A thin object that takes a :class:`~agentscope.app.message_bus.
MessageBus` and exposes the three composite operations the wake-up
flow actually needs (``enqueue`` / ``drain`` / ``subscribe_signal``).
Implemented as plain calls to the bus's transport-level primitives —
:meth:`MessageBus.queue_push`, :meth:`MessageBus.queue_drain`,
:meth:`MessageBus.publish`, :meth:`MessageBus.subscribe` — so no
domain method has to live on the bus.

This is the sample we point at when discussing how Phase 5 should
work for the index pipeline: domain helpers belong next to the
domain code, not as methods on the transport.
"""
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING

from ._wakeup_keys import (
    WAKEUP_QUEUE_KEY,
    WAKEUP_SIGNAL_CHANNEL,
    WakeupPayload,
)

if TYPE_CHECKING:
    from ..message_bus import MessageBus


class WakeupBroker:
    """Composes bus primitives into the wake-up flow.

    Args:
        message_bus (`MessageBus`):
            Application message bus whose primitives the broker
            composes.

    Note:
        The broker has no lifecycle of its own — it owns no
        subscriptions or background tasks. Multiple instances can
        coexist; the underlying bus state (the queue + the channel)
        is the single shared resource.
    """

    def __init__(self, message_bus: "MessageBus") -> None:
        self._bus = message_bus

    async def enqueue(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Enqueue a wake-up and fire the signal.

        Producers (``TeamSay``, ``AgentCreate``, the scheduler
        trigger, the BG-tool completion watcher, …) call this after
        depositing a message in the recipient's inbox. The shared
        :class:`~agentscope.app._manager.WakeupDispatcher` drains
        the queue on each signal.

        Args:
            user_id (`str`):
                Owning user id.
            session_id (`str`):
                Session to wake.
            agent_id (`str`):
                Agent id that owns the session.
        """
        payload: WakeupPayload = {
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
        }
        await self._bus.queue_push(WAKEUP_QUEUE_KEY, dict(payload))
        await self._bus.publish(WAKEUP_SIGNAL_CHANNEL, {})

    async def drain(self, max_count: int = 64) -> list[WakeupPayload]:
        """Drain pending wake-up entries from the shared queue.

        Args:
            max_count (`int`, defaults to ``64``):
                Maximum entries to drain per call.

        Returns:
            `list[WakeupPayload]`:
                Payloads in enqueue order.
        """
        entries = await self._bus.queue_drain(
            WAKEUP_QUEUE_KEY,
            max_count=max_count,
        )
        return [payload for _entry_id, payload in entries]  # type: ignore[misc]

    async def subscribe_signal(
        self,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Subscribe to the shared wake-up signal channel.

        Each yielded item means "drain the queue now"; the payload
        itself carries no business data.

        Args:
            on_ready (`Callable[[], None] | None`, optional):
                Forwarded to the underlying :meth:`MessageBus.subscribe`.

        Yields:
            `dict`:
                Opaque signal payload.
        """
        async for payload in self._bus.subscribe(
            WAKEUP_SIGNAL_CHANNEL,
            on_ready=on_ready,
        ):
            yield payload
