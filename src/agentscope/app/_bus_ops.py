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
    )


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
    kind: Literal["wake", "resume"] = MessageBusKeys.WAKEUP_KIND_WAKE,
    inputs: UserConfirmResultEvent
    | ExternalExecutionResultEvent
    | None = None,
) -> None:
    """Enqueue a typed run trigger and signal dispatchers.

    ``kind`` selects how the dispatcher handles the entry:

    - ``wake`` — idle-session wake-up.  The dispatcher skips the entry
      when the session is already running (the live run drains the inbox
      itself).  ``inputs`` must be ``None``.
    - ``resume`` — resume a HITL-parked session with a user confirmation
      or external execution result.  The dispatcher waits (with backoff)
      until the parked run releases its lock, then spawns with
      ``input_msg`` set to the deserialised event.

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
