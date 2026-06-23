# -*- coding: utf-8 -*-
"""Bus key constants and payload schema for the wake-up channel.

Centralised here so the :class:`MessageBus` itself does not have to
know what a "wake-up" is. Producers and consumers reach for these
constants directly when calling the bus primitives — there is no
``enqueue_wakeup`` method on the bus, and there should not be one.

The split is intentional: the bus owns transport-level primitives
(queue / pub-sub / log / lock); domain-shaped operations live in the
package that owns the domain. See ``WakeupBroker`` for the canonical
composition of these primitives.
"""
from typing import TypedDict


WAKEUP_QUEUE_KEY = "agentscope:wakeups"
"""Shared, durable wake-up queue. Producers ``queue_push`` here; the
single per-process :class:`~agentscope.app._manager.WakeupDispatcher`
``queue_drain`` it on each signal."""

WAKEUP_SIGNAL_CHANNEL = "agentscope:wakeup_signal"
"""Shared pub/sub channel that nudges every running dispatcher to
drain the queue. Payload is opaque — only its arrival matters."""


class WakeupPayload(TypedDict):
    """Shape of a single wake-up entry on the queue.

    Kept as a :class:`TypedDict` rather than a Pydantic model because
    the queue store is the wire format — we do not want runtime
    validation overhead per drain, and the schema is small enough
    that callers can hand-construct it.
    """

    user_id: str
    session_id: str
    agent_id: str
