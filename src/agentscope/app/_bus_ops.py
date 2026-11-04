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
   * - :func:`enqueue_chat_input`
     - Append an ordinary user turn to a per-session FIFO.
   * - :func:`enqueue_index_task`
     - Enqueue a knowledge-document indexing task and signal consumers.
"""
from __future__ import annotations

import json
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, TYPE_CHECKING, Literal

from .._logging import logger
from ..event import CustomEvent
from .message_bus._keys import MessageBusKeys
from ._chat_input_store import (
    cleanup_session,
    discard_input,
    decode_input,
    queue_entry,
)

if TYPE_CHECKING:
    from .message_bus._base import MessageBus

    from agentscope.event import (
        ExternalExecutionResultEvent,
        UserConfirmResultEvent,
        UserInterruptEvent,
    )
    from agentscope.message import Msg


# ── publish_session_event ──────────────────────────────────────────────


class ChatQueueFullError(RuntimeError):
    """Raised when a session has reached its pending-turn limit."""


class ChatQueueBusyError(RuntimeError):
    """Raised when a short queue mutation lock cannot be acquired."""


class ChatSteeringUnavailableError(RuntimeError):
    """Raised when a queued turn cannot steer the requested active reply."""


@asynccontextmanager
async def chat_input_mutation(
    bus: "MessageBus",
    session_id: str,
    *,
    timeout_secs: float | None = None,
) -> AsyncGenerator[None, None]:
    """Acquire a short-lived queue mutation lock with bounded waiting.

    Args:
        bus (`MessageBus`):
            Application message bus that owns the distributed lock.
        session_id (`str`):
            Session whose pending-input queue will be mutated.
        timeout_secs (`float | None`, optional):
            Maximum time to wait for the lock. ``None`` uses the configured
            queue-mutation timeout.

    Yields:
        `None`:
            Control while the session mutation lock is held.

    Raises:
        `ChatQueueBusyError`:
            The lock could not be acquired before the timeout.
    """
    timeout = (
        MessageBusKeys.CHAT_INPUT_MUTATION_TIMEOUT_SECS
        if timeout_secs is None
        else timeout_secs
    )
    async with AsyncExitStack() as stack:
        try:
            await stack.enter_async_context(
                bus.acquire_lock_bounded(
                    MessageBusKeys.chat_input_mutation_lock(session_id),
                    ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
                    timeout_secs=timeout,
                ),
            )
        except TimeoutError as exc:
            raise ChatQueueBusyError(
                "The pending message queue is busy; retry shortly.",
            ) from exc
        yield


@asynccontextmanager
async def _chat_input_user_quota(
    bus: "MessageBus",
    user_id: str,
) -> AsyncGenerator[None, None]:
    """Serialize the bounded per-user quota check performed by enqueue.

    Args:
        bus (`MessageBus`):
            Application message bus that owns the distributed lock.
        user_id (`str`):
            User whose aggregate pending-input quota is being checked.

    Yields:
        `None`:
            Control while the per-user quota lock is held.

    Raises:
        `ChatQueueBusyError`:
            The quota lock could not be acquired before the timeout.
    """
    async with AsyncExitStack() as stack:
        try:
            await stack.enter_async_context(
                bus.acquire_lock_bounded(
                    MessageBusKeys.chat_input_user_quota_lock(user_id),
                    ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
                    timeout_secs=(
                        MessageBusKeys.CHAT_INPUT_MUTATION_TIMEOUT_SECS
                    ),
                ),
            )
        except TimeoutError as exc:
            raise ChatQueueBusyError(
                "The pending message quota is busy; retry shortly.",
            ) from exc
        yield


def _serialized_input(
    inputs: "Msg | list[Msg]",
) -> dict | list[dict]:
    """Serialize one ordinary input without imposing a queue byte limit.

    Args:
        inputs (`Msg | list[Msg]`):
            One message or a non-empty ordered message list.

    Returns:
        `dict | list[dict]`:
            JSON-compatible message payload suitable for the bus.

    Raises:
        `ValueError`:
            ``inputs`` is an empty list.
    """
    if isinstance(inputs, list) and not inputs:
        raise ValueError("A queued message list must not be empty.")
    return (
        [msg.model_dump(mode="json") for msg in inputs]
        if isinstance(inputs, list)
        else inputs.model_dump(mode="json")
    )


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
        "queued_message",
    ] = MessageBusKeys.WAKEUP_KIND_WAKE,
    inputs: UserConfirmResultEvent
    | ExternalExecutionResultEvent
    | UserInterruptEvent
    | Msg
    | None = None,
    signal: bool = True,
) -> None:
    """Enqueue a typed run trigger and optionally signal dispatchers.

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
    - ``queued_message`` — signal that a session's ordinary browser-input
      FIFO should be drained. ``inputs`` must be ``None``.

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
            The input for ``resume`` and ``message`` triggers. Ignored (and
            should be ``None``) for ``wake`` and ``queued_message``. The
            function calls ``model_dump(mode="json")`` internally — callers
            pass the model object, not a pre-serialised dict.
        signal:
            Whether to publish the shared wakeup signal after pushing.
            Recovery sweeps pass ``False`` to batch many durable triggers
            behind one signal.
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
    if signal:
        await bus.publish(MessageBusKeys.wakeup_signal(), {})


async def nudge_chat_inputs(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
) -> None:
    """Wake pending input after releasing a run lock; recovery is fallback."""
    try:
        pending = await bus.queue_read(
            MessageBusKeys.chat_inputs(session_id),
            max_count=1,
        )
        claimed = await bus.registry_exists(
            MessageBusKeys.chat_input_inflight_registry(),
            session_id,
        )
        if pending or claimed:
            await enqueue_run_trigger(
                bus,
                user_id,
                session_id,
                agent_id,
                kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
            )
    except Exception:
        logger.exception("Failed to nudge queued inputs for %s.", session_id)


async def enqueue_chat_input(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
    inputs: Msg | list[Msg],
) -> dict:
    """Append an ordinary user turn to a session FIFO and wake its pump.

    The input and its wake-up marker are deliberately separate: the
    per-session queue owns ordering, while the shared trigger queue only
    nudges an available dispatcher. Duplicate ``queued_message`` triggers
    are harmless because the per-session queue is consumed exactly once.
    Once the input is stored, notification failures are logged rather than
    reported as rejection; pending-input recovery retries dispatch.

    Args:
        bus:
            Application message bus.
        user_id:
            Owning user id.
        session_id:
            Target session id.
        agent_id:
            Agent that owns the session.
        inputs:
            One user message or a non-empty ordered message list.

    Returns:
        `dict`:
            Public queue item containing ``id``, ``created_at``, and
            serialized ``input``.

    Raises:
        `ValueError`:
            ``inputs`` is an empty list.
        `ChatQueueFullError`:
            The session queue or aggregate per-user quota is full.
        `ChatQueueBusyError`:
            A queue or quota mutation lock cannot be acquired promptly.
    """
    serialized = _serialized_input(inputs)
    item: dict = {
        # Queue identity is independent from message identity. Clients may
        # legitimately retry/replay the same Msg.id; management operations
        # still need one unambiguous id per pending turn.
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "session_id": session_id,
        "agent_id": agent_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": serialized,
        "state": "queued",
    }
    async with _chat_input_user_quota(bus, user_id):
        if (
            await _count_user_chat_inputs(bus, user_id)
            >= MessageBusKeys.CHAT_INPUT_USER_MAX_LEN
        ):
            raise ChatQueueFullError(
                "Your pending message quota is full; wait for a message "
                "to finish or remove one before sending another.",
            )
        async with chat_input_mutation(bus, session_id):
            existing = await bus.queue_read(
                MessageBusKeys.chat_inputs(session_id),
                max_count=MessageBusKeys.CHAT_INPUT_MAX_LEN,
            )
            if len(existing) >= MessageBusKeys.CHAT_INPUT_MAX_LEN:
                raise ChatQueueFullError(
                    "The pending message queue is full; wait for a message "
                    "to start or remove one before sending another.",
                )
            # Register before pushing. If the process dies between these two
            # operations recovery sees a harmless stale marker and removes it;
            # the inverse order could leave an unindexed queue permanently
            # stranded after a lost trigger.
            await bus.registry_set(
                MessageBusKeys.chat_input_pending_registry(),
                session_id,
                json.dumps(
                    {
                        "user_id": user_id,
                        "agent_id": agent_id,
                    },
                ),
            )
            await bus.registry_set(
                MessageBusKeys.chat_input_user_sessions(user_id),
                session_id,
                agent_id,
            )
            item["input_ref"] = item["id"]
            await bus.registry_set(
                MessageBusKeys.chat_input_payloads(session_id),
                item["input_ref"],
                json.dumps(serialized),
            )
            await bus.queue_push(
                MessageBusKeys.chat_inputs(session_id),
                queue_entry(item),
            )
            try:
                await _publish_chat_queue_changed(bus, session_id)
            except Exception:
                logger.exception(
                    "Chat input %s accepted, but queue notification failed.",
                    item["id"],
                )
    try:
        await enqueue_run_trigger(
            bus,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
        )
    except Exception:
        logger.exception(
            "Chat input %s accepted; wakeup failed, awaiting recovery.",
            item["id"],
        )
    return _public_chat_input(item)


async def list_chat_inputs(
    bus: "MessageBus",
    session_id: str,
) -> list[dict]:
    """Return editable pending turns for a session in FIFO order.

    Args:
        bus (`MessageBus`):
            Application message bus.
        session_id (`str`):
            Session whose pending queue should be inspected.

    Returns:
        `list[dict]`:
            Public queue items ordered from the next turn to the last.
    """
    payloads = await _read_chat_input_payloads(bus, session_id)
    return [_public_chat_input(payload) for payload in payloads]


async def register_active_chat_reply(
    bus: "MessageBus",
    session_id: str,
    reply_id: str,
) -> None:
    """Register the reply currently holding a session's run lock.

    Internal lifecycle writes wait for the mutation lock instead of using the
    bounded foreground timeout. The registry entry is the authoritative target
    checked by the public Steer endpoint.

    Args:
        bus (`MessageBus`):
            Application message bus.
        session_id (`str`):
            Session whose reply started or resumed.
        reply_id (`str`):
            Active reply identifier.
    """
    async with bus.acquire_lock(
        MessageBusKeys.chat_input_mutation_lock(session_id),
        ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
    ):
        await bus.registry_set(
            MessageBusKeys.chat_active_reply_registry(),
            session_id,
            reply_id,
        )


async def steer_chat_input(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
    item_id: str,
    reply_id: str,
) -> list[dict]:
    """Reserve one pending queue item for the requested active reply.

    The item stays in the durable FIFO until middleware acknowledges actual
    context injection. Repeating the same request is idempotent.

    Args:
        bus (`MessageBus`):
            Application message bus.
        user_id (`str`):
            User that must own the pending item.
        session_id (`str`):
            Session that owns the queue and active reply.
        agent_id (`str`):
            Agent that must own the pending item.
        item_id (`str`):
            Pending queue item to steer with.
        reply_id (`str`):
            Reply observed as active by the client.

    Returns:
        `list[dict]`:
            Complete public queue snapshot after reservation.

    Raises:
        `LookupError`:
            The item is no longer pending.
        `ChatSteeringUnavailableError`:
            The reply is stale, idle, or the item targets another reply.
        `ChatQueueBusyError`:
            The foreground mutation lock cannot be acquired promptly.
    """
    async with chat_input_mutation(bus, session_id):
        active_replies = await bus.registry_getall(
            MessageBusKeys.chat_active_reply_registry(),
        )
        active_reply_id = active_replies.get(session_id)
        is_running = await bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        )
        if not is_running or active_reply_id != reply_id:
            raise ChatSteeringUnavailableError(
                f"Reply '{reply_id}' is no longer active.",
            )

        payloads = await _read_chat_input_payloads(bus, session_id)
        item = _owned_chat_input(
            payloads,
            user_id,
            session_id,
            agent_id,
            item_id,
        )
        item_state = item.get("state", "queued")
        target_reply_id = item.get("target_reply_id")
        if item_state == "steering":
            if target_reply_id != reply_id:
                raise ChatSteeringUnavailableError(
                    f"Queued message '{item_id}' is steering another reply.",
                )
            return [_public_chat_input(payload) for payload in payloads]
        if item_state not in ("queued", "failed"):
            raise ChatSteeringUnavailableError(
                f"Queued message '{item_id}' cannot be steered now.",
            )

        item["state"] = "steering"
        item["target_reply_id"] = reply_id
        item.pop("error", None)
        await bus.queue_replace(
            MessageBusKeys.chat_inputs(session_id),
            [queue_entry(payload) for payload in payloads],
        )
        return await _notify_chat_queue_changed(bus, session_id, payloads)


async def list_steering_chat_inputs(
    bus: "MessageBus",
    session_id: str,
    reply_id: str,
) -> list[dict]:
    """Read items reserved for one reply without removing them.

    Args:
        bus (`MessageBus`):
            Application message bus.
        session_id (`str`):
            Session whose queue should be inspected.
        reply_id (`str`):
            Active reply that must own the steering reservation.

    Returns:
        `list[dict]`:
            Public copies of matching items in FIFO order.
    """
    async with bus.acquire_lock(
        MessageBusKeys.chat_input_mutation_lock(session_id),
        ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
    ):
        payloads = await _read_chat_input_payloads(bus, session_id)
        return [
            _public_chat_input(payload)
            for payload in payloads
            if payload.get("state", "queued") == "steering"
            and payload.get("target_reply_id") == reply_id
        ]


async def acknowledge_steering_chat_inputs(
    bus: "MessageBus",
    session_id: str,
    reply_id: str,
    item_ids: list[str],
) -> list[dict]:
    """Remove steering items after they were appended to agent context.

    Args:
        bus (`MessageBus`):
            Application message bus.
        session_id (`str`):
            Session whose FIFO contains the items.
        reply_id (`str`):
            Reply that consumed the items.
        item_ids (`list[str]`):
            Exact queue item ids to acknowledge.

    Returns:
        `list[dict]`:
            Public copies of the removed items.

    Raises:
        `LookupError`:
            Any requested item is no longer reserved for this reply.
    """
    requested_ids = set(item_ids)
    async with bus.acquire_lock(
        MessageBusKeys.chat_input_mutation_lock(session_id),
        ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
    ):
        active_replies = await bus.registry_getall(
            MessageBusKeys.chat_active_reply_registry(),
        )
        if active_replies.get(session_id) != reply_id:
            raise LookupError(
                f"Reply '{reply_id}' is no longer active.",
            )
        payloads = await _read_chat_input_payloads(bus, session_id)
        removed = [
            payload
            for payload in payloads
            if payload.get("id") in requested_ids
            and payload.get("state", "queued") == "steering"
            and payload.get("target_reply_id") == reply_id
        ]
        if {payload.get("id") for payload in removed} != requested_ids:
            raise LookupError(
                "One or more steering messages are no longer pending.",
            )
        remaining = [payload for payload in payloads if payload not in removed]
        await bus.queue_replace(
            MessageBusKeys.chat_inputs(session_id),
            [queue_entry(payload) for payload in remaining],
        )
        for payload in removed:
            await discard_input(bus, payload)
        await _notify_chat_queue_changed(bus, session_id, remaining)
        return [_public_chat_input(payload) for payload in removed]


async def fail_steering_chat_input(
    bus: "MessageBus",
    session_id: str,
    reply_id: str,
    item_id: str,
    error: str,
) -> None:
    """Keep a failed steering item in the FIFO with a visible error.

    Args:
        bus (`MessageBus`):
            Application message bus.
        session_id (`str`):
            Session whose queue owns the item.
        reply_id (`str`):
            Reply the item attempted to steer.
        item_id (`str`):
            Failed queue item id.
        error (`str`):
            User-facing failure description.
    """
    async with bus.acquire_lock(
        MessageBusKeys.chat_input_mutation_lock(session_id),
        ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
    ):
        payloads = await _read_chat_input_payloads(bus, session_id)
        changed = False
        for payload in payloads:
            if (
                payload.get("id") == item_id
                and payload.get("state", "queued") == "steering"
                and payload.get("target_reply_id") == reply_id
            ):
                payload["state"] = "failed"
                payload["error"] = error
                payload.pop("target_reply_id", None)
                changed = True
                break
        if changed:
            await bus.queue_replace(
                MessageBusKeys.chat_inputs(session_id),
                [queue_entry(payload) for payload in payloads],
            )
            await _notify_chat_queue_changed(bus, session_id, payloads)


async def finish_active_chat_reply(
    bus: "MessageBus",
    session_id: str,
    reply_id: str,
) -> list[str]:
    """Clear an active reply and restore its unconsumed steering items.

    Args:
        bus (`MessageBus`):
            Application message bus.
        session_id (`str`):
            Session whose run is finishing.
        reply_id (`str`):
            Reply whose active registry entry should be cleared.

    Returns:
        `list[str]`:
            Queue item ids restored to deferred-send state.
    """
    async with bus.acquire_lock(
        MessageBusKeys.chat_input_mutation_lock(session_id),
        ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
    ):
        active_replies = await bus.registry_getall(
            MessageBusKeys.chat_active_reply_registry(),
        )
        if active_replies.get(session_id) != reply_id:
            return []
        await bus.registry_del(
            MessageBusKeys.chat_active_reply_registry(),
            session_id,
        )
        payloads = await _read_chat_input_payloads(bus, session_id)
        restored_ids: list[str] = []
        for payload in payloads:
            if (
                payload.get("state", "queued") == "steering"
                and payload.get("target_reply_id") == reply_id
            ):
                payload["state"] = "queued"
                payload.pop("target_reply_id", None)
                payload.pop("error", None)
                restored_ids.append(payload["id"])
        if restored_ids:
            await bus.queue_replace(
                MessageBusKeys.chat_inputs(session_id),
                [queue_entry(payload) for payload in payloads],
            )
            await _notify_chat_queue_changed(bus, session_id, payloads)
        return restored_ids


async def update_chat_input(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
    item_id: str,
    inputs: Msg | list[Msg],
) -> list[dict]:
    """Replace one still-pending turn and return the updated queue.

    Args:
        bus (`MessageBus`):
            Application message bus.
        user_id (`str`):
            User that must own the pending item.
        session_id (`str`):
            Session that owns the pending queue.
        agent_id (`str`):
            Agent that must own the pending item.
        item_id (`str`):
            Stable business id of the item to update.
        inputs (`Msg | list[Msg]`):
            Replacement message or non-empty ordered message list.

    Returns:
        `list[dict]`:
            Complete public queue snapshot after the update.

    Raises:
        `ValueError`:
            ``inputs`` is an empty list.
        `LookupError`:
            The item is no longer pending or is not owned by the caller.
        `ChatQueueBusyError`:
            The session mutation lock cannot be acquired promptly.
    """
    serialized = _serialized_input(inputs)
    async with chat_input_mutation(bus, session_id):
        payloads = await _read_chat_input_payloads(bus, session_id)
        item = _owned_chat_input(
            payloads,
            user_id,
            session_id,
            agent_id,
            item_id,
        )
        if item.get("state", "queued") == "steering":
            raise LookupError(
                f"Queued message '{item_id}' is currently steering.",
            )
        previous = dict(item)
        item["input"] = serialized
        item["state"] = "queued"
        item.pop("target_reply_id", None)
        item.pop("error", None)
        item["input_ref"] = uuid.uuid4().hex
        await bus.registry_set(
            MessageBusKeys.chat_input_payloads(session_id),
            item["input_ref"],
            json.dumps(serialized),
        )
        await bus.queue_replace(
            MessageBusKeys.chat_inputs(session_id),
            [queue_entry(payload) for payload in payloads],
        )
        await discard_input(bus, previous)
        return await _notify_chat_queue_changed(bus, session_id, payloads)


async def delete_chat_input(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
    item_id: str,
) -> list[dict]:
    """Delete one still-pending turn and return the updated queue.

    Args:
        bus (`MessageBus`):
            Application message bus.
        user_id (`str`):
            User that must own the pending item.
        session_id (`str`):
            Session that owns the pending queue.
        agent_id (`str`):
            Agent that must own the pending item.
        item_id (`str`):
            Stable business id of the item to delete.

    Returns:
        `list[dict]`:
            Complete public queue snapshot after deletion.

    Raises:
        `LookupError`:
            The item is no longer pending or is not owned by the caller.
        `ChatQueueBusyError`:
            The session mutation lock cannot be acquired promptly.
    """
    async with chat_input_mutation(bus, session_id):
        payloads = await _read_chat_input_payloads(bus, session_id)
        item = _owned_chat_input(
            payloads,
            user_id,
            session_id,
            agent_id,
            item_id,
        )
        if item.get("state", "queued") == "steering":
            raise LookupError(
                f"Queued message '{item_id}' is currently steering.",
            )
        payloads.remove(item)
        await bus.queue_replace(
            MessageBusKeys.chat_inputs(session_id),
            [queue_entry(payload) for payload in payloads],
        )
        await discard_input(bus, item)
        return await _notify_chat_queue_changed(bus, session_id, payloads)


async def reorder_chat_inputs(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
    item_ids: list[str],
) -> list[dict]:
    """Atomically replace the pending queue order.

    ``item_ids`` must be an exact permutation of the queue snapshot. If a
    turn started or another client changed the queue in the meantime the
    operation fails instead of silently dropping or duplicating an item.

    Args:
        bus (`MessageBus`):
            Application message bus.
        user_id (`str`):
            User whose owned items may be reordered.
        session_id (`str`):
            Session that owns the pending queue.
        agent_id (`str`):
            Agent whose owned items may be reordered.
        item_ids (`list[str]`):
            Exact desired permutation of the caller-owned pending item ids.

    Returns:
        `list[dict]`:
            Complete public queue snapshot after reordering.

    Raises:
        `ValueError`:
            The supplied ids contain duplicates or are not an exact
            permutation of the current owned snapshot.
        `ChatQueueBusyError`:
            The session mutation lock cannot be acquired promptly.
    """
    async with chat_input_mutation(bus, session_id):
        payloads = await _read_chat_input_payloads(bus, session_id)
        if any(
            payload.get("state", "queued") == "steering"
            for payload in payloads
        ):
            raise ValueError(
                "The queue cannot be reordered while a message is steering.",
            )
        owned = [
            payload
            for payload in payloads
            if payload.get("user_id") == user_id
            and payload.get("session_id") == session_id
            and payload.get("agent_id") == agent_id
        ]
        current_ids = [str(payload.get("id")) for payload in owned]
        if (
            len(item_ids) != len(set(item_ids))
            or set(item_ids) != set(current_ids)
            or len(item_ids) != len(current_ids)
        ):
            raise ValueError(
                "The pending queue changed; refresh it before reordering.",
            )
        by_id = {str(payload["id"]): payload for payload in owned}
        reordered_owned = iter(by_id[item_id] for item_id in item_ids)
        # Preserve payloads outside this caller's ownership at their exact
        # positions. They indicate corrupt/legacy state, but a defensive
        # reorder must never erase them.
        reordered = [
            next(reordered_owned)
            if (
                payload.get("user_id") == user_id
                and payload.get("session_id") == session_id
                and payload.get("agent_id") == agent_id
            )
            else payload
            for payload in payloads
        ]
        await bus.queue_replace(
            MessageBusKeys.chat_inputs(session_id),
            [queue_entry(payload) for payload in reordered],
        )
        return await _notify_chat_queue_changed(
            bus,
            session_id,
            reordered,
        )


async def _read_chat_input_payloads(
    bus: "MessageBus",
    session_id: str,
) -> list[dict]:
    """Hydrate editable entries, omitting claims and deleted references.

    Args:
        bus (`MessageBus`):
            Application message bus.
        session_id (`str`):
            Session whose raw queue payloads should be read.

    Returns:
        `list[dict]`:
            Raw oldest-first queue payloads, including routing metadata.
    """
    entries = await bus.queue_read(
        MessageBusKeys.chat_inputs(session_id),
        max_count=MessageBusKeys.CHAT_INPUT_MAX_LEN,
    )
    # A failed dequeue can leave a durable claim in the physical FIFO.
    # Mutations call this reader under the same lock as claim creation;
    # they must never replace or discard the claimed input reference.
    raw_claim = await bus.registry_get(
        MessageBusKeys.chat_input_claim(session_id),
        "claim",
    )
    if raw_claim is not None:
        try:
            claimed_id = json.loads(raw_claim)["payload"]["id"]
        except (KeyError, TypeError, ValueError):
            logger.warning("Invalid chat-input claim for %s.", session_id)
        else:
            entries = [
                entry for entry in entries if entry[1].get("id") != claimed_id
            ]
    refs = [
        entry["input_ref"]
        for _, entry in entries
        if "input" not in entry and isinstance(entry.get("input_ref"), str)
    ]
    values = (
        await bus.registry_get_many(
            MessageBusKeys.chat_input_payloads(session_id),
            refs,
        )
        if refs
        else []
    )
    inputs = dict(zip(refs, values))
    payloads = []
    for _entry_id, entry in entries:
        payload = (
            entry
            if "input" in entry
            else decode_input(
                entry,
                inputs.get(entry.get("input_ref")),
            )
        )
        if payload is not None:
            payloads.append(payload)
    return payloads


async def _count_user_chat_inputs(
    bus: "MessageBus",
    user_id: str,
) -> int:
    """Count one user's pending and claimed turns across all sessions.

    Args:
        bus (`MessageBus`):
            Application message bus.
        user_id (`str`):
            User whose aggregate queue usage should be counted.

    Returns:
        `int`:
            Pending and distinct in-flight turns, capped once the configured
            per-user quota is reached.
    """
    sessions = await bus.registry_getall(
        MessageBusKeys.chat_input_user_sessions(user_id),
    )
    count = 0
    for session_id in sessions:
        entries = await bus.queue_read(
            MessageBusKeys.chat_inputs(session_id),
            max_count=MessageBusKeys.CHAT_INPUT_MAX_LEN,
        )
        ids = {payload["id"] for _, payload in entries}
        raw = await bus.registry_get(
            MessageBusKeys.chat_input_inflight_registry(),
            session_id,
        )
        if raw is not None:
            try:
                item_id = json.loads(raw)["id"]
                if not isinstance(item_id, str) or not item_id:
                    raise ValueError("Invalid claimed item id.")
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "Invalid quota claim for session %s; counting one item.",
                    session_id,
                )
                count += 1
            else:
                ids.add(item_id)
        count += len(ids)
        if count >= MessageBusKeys.CHAT_INPUT_USER_MAX_LEN:
            return count
    return count


def _owned_chat_input(
    payloads: list[dict],
    user_id: str,
    session_id: str,
    agent_id: str,
    item_id: str,
) -> dict:
    """Resolve an owned pending item or report that it already started.

    Args:
        payloads (`list[dict]`):
            Raw pending queue snapshot.
        user_id (`str`):
            User that must own the item.
        session_id (`str`):
            Session that must own the item.
        agent_id (`str`):
            Agent that must own the item.
        item_id (`str`):
            Stable business id to resolve.

    Returns:
        `dict`:
            Matching mutable raw payload from ``payloads``.

    Raises:
        `LookupError`:
            No matching, caller-owned pending item exists.
    """
    for payload in payloads:
        if (
            payload.get("id") == item_id
            and payload.get("user_id") == user_id
            and payload.get("session_id") == session_id
            and payload.get("agent_id") == agent_id
        ):
            return payload
    raise LookupError(
        f"Queued message '{item_id}' is no longer pending.",
    )


def _public_chat_input(payload: dict) -> dict:
    """Strip routing metadata from one queue item returned to clients.

    Args:
        payload (`dict`):
            Raw queue payload containing identity, routing, and input data.

    Returns:
        `dict`:
            Client-safe ``id``, ``created_at``, and ``input`` fields.
    """
    return {
        "id": payload["id"],
        "created_at": payload["created_at"],
        "input": payload["input"],
        "state": payload.get("state", "queued"),
        "error": payload.get("error"),
    }


async def _publish_chat_queue_changed(
    bus: "MessageBus",
    session_id: str,
    payloads: list[dict] | None = None,
) -> list[dict]:
    """Publish a complete queue snapshot and return its public items.

    Args:
        bus (`MessageBus`):
            Application message bus used for the live fan-out.
        session_id (`str`):
            Session whose subscribers receive the snapshot.
        payloads (`list[dict] | None`, optional):
            Raw snapshot to publish. ``None`` reads the current queue and
            therefore requires the caller to hold the mutation lock.

    Returns:
        `list[dict]`:
            Public queue items included in the live event.
    """
    if payloads is None:
        payloads = await _read_chat_input_payloads(bus, session_id)
    if not payloads:
        await cleanup_session(bus, session_id)
    items = [_public_chat_input(payload) for payload in payloads]
    event = CustomEvent(name="chat_queue_changed", value={"items": items})
    # Queue state is an ephemeral projection backed by GET /chat/queue.
    # Publishing it live avoids O(n²) replay-log growth and stale snapshots
    # temporarily rolling the UI backwards after an SSE reconnect.
    await bus.publish(
        MessageBusKeys.session_events(session_id),
        event.model_dump(mode="json"),
    )
    return items


async def _notify_chat_queue_changed(
    bus: "MessageBus",
    session_id: str,
    payloads: list[dict],
) -> list[dict]:
    """Return committed state even when its live notification fails."""
    items = [_public_chat_input(payload) for payload in payloads]
    try:
        await _publish_chat_queue_changed(bus, session_id, payloads)
    except Exception:
        logger.exception(
            "Queue mutation committed; notification failed for %s.",
            session_id,
        )
    return items


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
