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
