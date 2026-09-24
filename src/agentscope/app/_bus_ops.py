# -*- coding: utf-8 -*-
"""Business-level operations built on top of MessageBus primitives.

These helpers compose generic bus primitives (``log_append``, ``publish``,
``queue_push``) with domain-specific key layouts from ``MessageBusKeys``.
They live here — between the transport layer (``message_bus``) and the
service layer (``_service``) — so that neither layer needs to know about the
other's internals.

.. list-table::
   :widths: 30 70

   * - :func:`publish_session_event`
     - Append an event to the session replay log and fan it out live.
   * - :func:`enqueue_run_trigger`
     - Enqueue a typed run trigger and signal dispatchers.
   * - :func:`enqueue_index_task`
     - Enqueue a knowledge-document indexing task and signal consumers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .message_bus._keys import MessageBusKeys

if TYPE_CHECKING:
    from .message_bus._base import MessageBus

    from agentscope.event import (
        ExternalExecutionResultEvent,
        UserConfirmResultEvent,
        UserInterruptEvent,
    )
    from agentscope.message import Msg


# ── publish_session_event ──────────────────────────────────────────────


async def publish_session_event(
    bus: "MessageBus",
    session_id: str,
    event: dict,
) -> str:
    """Append event to replay log + fan out live.

    Args:
        bus (`MessageBus`):
            The application message bus.
        session_id (`str`):
            The session this event belongs to.
        event (`dict`):
            JSON-serializable event payload.

    Returns:
        `str`:
            The replay-log entry id assigned by the backend.
    """
    key = MessageBusKeys.session_events(session_id)
    entry_id = await bus.log_append(
        key,
        event,
        max_len=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
    )
    await bus.publish(key, {**event, "_entry_id": entry_id})
    return entry_id


# ── enqueue_run_trigger ────────────────────────────────────────────────


async def enqueue_run_trigger(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
    *,
    kind: Literal[
        "wake",
        "resume",
        "message",
    ] = MessageBusKeys.WAKEUP_KIND_WAKE,
    inputs: UserConfirmResultEvent
    | ExternalExecutionResultEvent
    | UserInterruptEvent
    | Msg
    | None = None,
) -> None:
    """Enqueue a typed run trigger and signal dispatchers.

    ``kind`` selects how the dispatcher handles the entry:

    - ``wake`` — idle-session wake-up.  The dispatcher skips the entry
      when the session is already running (the live run drains the inbox
      itself).  ``inputs`` must be ``None``.
    - ``resume`` — resume a HITL-parked session with a user confirmation,
      an external execution result, or a user interrupt.  The dispatcher
      waits (with backoff) until the parked run releases its lock, then
      spawns with ``input_msg`` set to the deserialised event.
    - ``message`` — start a new turn from a genuine user ``Msg`` (e.g. an
      inbound channel message).  Like ``resume`` it carries input and is
      re-queued rather than dropped while the session is running; the run
      persists it and reasons over it as a real user turn.

    The payload is serialised to a plain dict before being pushed to the
    wakeup queue; the ``MessageBus`` transport layer never sees event
    types.

    Args:
        bus (`MessageBus`):
            The application message bus.
        user_id (`str`):
            The owning user id.
        session_id (`str`):
            The session to trigger a run for.
        agent_id (`str`):
            The agent id that owns the session.
        kind:
            Trigger kind.  Defaults to ``"wake"``.
        inputs:
            The input event for ``resume`` triggers.  Ignored (and
            should be ``None``) for ``wake``.  The function calls
            ``model_dump(mode="json")`` internally — callers pass the
            event object, not a pre-serialised dict.
    """
    await bus.queue_push(
        MessageBusKeys.wakeup_queue(),
        {
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "kind": kind,
            "input": inputs.model_dump(mode="json") if inputs else None,
        },
    )
    await bus.publish(MessageBusKeys.wakeup_signal(), {})


# ── session inbox hand-off ─────────────────────────────────────────────
#
# Three helpers implementing one protocol, whose only job is to make
# sure a payload pushed to a session inbox is always consumed by *some*
# run rather than sitting there until the next user turn.
#
# The naive version — "push, then wake the session unless it looks
# busy" — loses entries: a run that already performed its last drain is
# still busy (it is streaming, persisting, releasing its lock), so the
# producer skips the wake-up while the run will never look again.
#
# The fix is to make two tiny critical sections mutually exclusive
# under :meth:`MessageBusKeys.inbox_lock`:
#
#   producer   push entry            → read consumer flag
#   consumer   drain remaining entries → clear consumer flag
#
# Whichever runs first, the entry is covered. Producer first → the
# consumer's drain sees the entry and keeps going. Consumer first →
# the flag is already clear, so the producer enqueues a wake-up.
# Because the wake-up is only produced when no consumer is registered,
# it never spawns a run that has nothing to do.


async def deliver_to_inbox(
    bus: "MessageBus",
    *,
    user_id: str,
    session_id: str,
    agent_id: str,
    payload: dict,
) -> None:
    """Push a payload to a session inbox and wake the session if no run
    is currently consuming it.

    Args:
        bus (`MessageBus`):
            The application message bus.
        user_id (`str`):
            The owning user id.
        session_id (`str`):
            The session whose inbox receives the payload.
        agent_id (`str`):
            The agent that owns the session.
        payload (`dict`):
            JSON-serialisable payload, normally a serialised
            :class:`~agentscope.message.HintBlock`.
    """
    async with bus.acquire_lock(
        MessageBusKeys.inbox_lock(session_id),
        ttl_secs=MessageBusKeys.INBOX_LOCK_TTL_SECS,
    ):
        await bus.queue_push(MessageBusKeys.inbox(session_id), payload)
        consumer = await bus.registry_get(
            MessageBusKeys.inbox_consumer(session_id),
            MessageBusKeys.INBOX_CONSUMER_FIELD,
        )

    if consumer is None:
        await enqueue_run_trigger(
            bus,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )


async def register_inbox_consumer(bus: "MessageBus", session_id: str) -> None:
    """Mark this run as the consumer of ``session_id``'s inbox.

    Call once per run, as early as possible — any producer that pushes
    after this point relies on this run draining the entry rather than
    enqueueing its own wake-up.

    The flag carries the same lease as a chat run, so a process that
    dies mid-run stops suppressing wake-ups once the lease expires.

    Args:
        bus (`MessageBus`):
            The application message bus.
        session_id (`str`):
            The session being consumed.
    """
    await bus.registry_set(
        MessageBusKeys.inbox_consumer(session_id),
        MessageBusKeys.INBOX_CONSUMER_FIELD,
        "1",
        ttl_secs=MessageBusKeys.SESSION_RUN_TTL_SECS,
    )


async def has_pending_inbox_or_release(
    bus: "MessageBus",
    session_id: str,
) -> bool:
    """Report whether the inbox still holds anything, releasing the
    consumer registration when it does not.

    Call at the very end of a run, before it releases the session lock.
    ``True`` means the run must go around once more — it stays
    registered as the consumer, so producers keep deferring to it
    instead of enqueuing their own wake-up. ``False`` means the run may
    finish: the registration is gone, so the next producer wakes the
    session itself.

    The check reads by draining and putting everything straight back,
    because the bus deliberately exposes no non-destructive peek. Both
    halves happen under :meth:`MessageBusKeys.inbox_lock`, which every
    producer also holds while pushing, so nothing can slip in between
    and arrival order is preserved.

    Args:
        bus (`MessageBus`):
            The application message bus.
        session_id (`str`):
            The session being consumed.

    Returns:
        `bool`:
            ``True`` when payloads remain to be consumed.
    """
    inbox = MessageBusKeys.inbox(session_id)
    async with bus.acquire_lock(
        MessageBusKeys.inbox_lock(session_id),
        ttl_secs=MessageBusKeys.INBOX_LOCK_TTL_SECS,
    ):
        payloads: list[dict] = []
        while True:
            batch = await bus.queue_drain(inbox, max_count=100)
            if not batch:
                break
            payloads.extend(payload for _entry_id, payload in batch)

        if not payloads:
            await bus.registry_del(
                MessageBusKeys.inbox_consumer(session_id),
                MessageBusKeys.INBOX_CONSUMER_FIELD,
            )
            return False

        for payload in payloads:
            await bus.queue_push(inbox, payload)
        return True


async def abandon_inbox_consumer(
    bus: "MessageBus",
    *,
    user_id: str,
    session_id: str,
    agent_id: str,
) -> None:
    """Give up the consumer registration without having drained the
    inbox, waking the session when payloads are still queued.

    Used when a run ends abnormally — an interrupt, a cancelled task, a
    failed turn. Producers deferred to this run while it was
    registered, so it cannot simply drop the registration and leave
    their payloads unattended.

    Args:
        bus (`MessageBus`):
            The application message bus.
        user_id (`str`):
            The owning user id.
        session_id (`str`):
            The session being abandoned.
        agent_id (`str`):
            The agent that owns the session.
    """
    pending = await has_pending_inbox_or_release(bus, session_id)
    if not pending:
        return

    await bus.registry_del(
        MessageBusKeys.inbox_consumer(session_id),
        MessageBusKeys.INBOX_CONSUMER_FIELD,
    )
    await enqueue_run_trigger(
        bus,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
    )


# ── enqueue_index_task ─────────────────────────────────────────────────


async def enqueue_index_task(
    bus: "MessageBus",
    user_id: str,
    knowledge_base_id: str,
    document_id: str,
) -> None:
    """Enqueue a knowledge-document indexing task and signal consumers.

    Pushes a structured payload onto the durable index-task queue and
    publishes a signal so any subscribed
    :class:`~agentscope.app._service.IndexTaskConsumer` drains it within
    one ``subscribe`` round-trip.

    The push happens *before* the publish so a worker woken by the
    signal is guaranteed to find the entry on its drain.  Re-enqueuing
    the same document is safe — the worker's lease CAS rejects
    duplicates — so the queue may legitimately hold multiple entries
    for the same document (one from upload, one from sweeper).

    Args:
        bus (`MessageBus`):
            The application message bus.
        user_id (`str`):
            The owning user id.
        knowledge_base_id (`str`):
            The parent knowledge base id.
        document_id (`str`):
            The document id to index.
    """
    await bus.queue_push(
        MessageBusKeys.index_tasks_queue(),
        {
            "user_id": user_id,
            "knowledge_base_id": knowledge_base_id,
            "document_id": document_id,
        },
    )
    await bus.publish(MessageBusKeys.index_tasks_signal(), {})
