# -*- coding: utf-8 -*-
"""Session-local payload storage and lightweight queue indexes."""
import json
from typing import TYPE_CHECKING

from .._logging import logger
from .message_bus import MessageBusKeys

if TYPE_CHECKING:
    from .message_bus import MessageBus
    from .storage import StorageBase


INPUT_EXECUTION = "_agentscope_input_execution"


async def get_submission(
    bus: "MessageBus",
    session_id: str,
    submission_id: str,
    user_id: str,
    agent_id: str,
) -> dict | None:
    """Read an owned submission under the session mutation lock."""
    raw = await bus.registry_get(
        MessageBusKeys.chat_submissions(session_id),
        submission_id,
    )
    if raw is None:
        return None
    record = json.loads(raw)
    if (record["user_id"], record["agent_id"]) != (user_id, agent_id):
        raise LookupError("Submission does not belong to this caller.")
    return record


async def is_input_cancelled(
    bus: "MessageBus",
    session_id: str,
    item_id: str,
    user_id: str,
    agent_id: str,
) -> bool:
    """Match an exact queue cancellation, never just a reused message ID."""
    raw = await bus.registry_get(
        MessageBusKeys.chat_input_cancellations(session_id),
        item_id,
    )
    if raw is None:
        return False
    record = json.loads(raw)
    return (record.get("user_id"), record.get("agent_id")) == (
        user_id,
        agent_id,
    )


async def finish_unbound_inputs(
    storage: "StorageBase",
    user_id: str,
    session_id: str,
    message_ids: list[str],
) -> None:
    """Record terminal inputs that never acquired a reply identity.

    Bound replies use their persisted end state instead: returning from
    a run may mean that a reply is parked for user confirmation.
    """
    for message_id in message_ids:
        msg = await storage.get_message(user_id, session_id, message_id)
        if msg is None:
            continue
        result = msg.metadata.get(INPUT_EXECUTION)
        if isinstance(result, dict) and not result.get("reply_id"):
            msg.metadata[INPUT_EXECUTION] = {
                "reply_id": None,
                "finished": True,
            }
            await storage.upsert_message(user_id, session_id, msg)


def queue_entry(payload: dict) -> dict:
    """Remove hydrated input from a reference-backed queue entry."""
    if "input_ref" not in payload:
        return payload
    return {key: value for key, value in payload.items() if key != "input"}


async def hydrate_input(bus: "MessageBus", payload: dict) -> dict | None:
    """Load one input reference; tolerate a concurrently deleted item."""
    if "input" in payload:
        return payload
    if "input_ref" not in payload:
        return None
    raw = await bus.registry_get(
        MessageBusKeys.chat_input_payloads(payload["session_id"]),
        payload["input_ref"],
    )
    if raw is None:
        return None
    return decode_input(payload, raw)


def decode_input(payload: dict, raw: str | None) -> dict | None:
    """Decode a referenced input, omitting missing or corrupt payloads."""
    if raw is None:
        return None
    try:
        return {**payload, "input": json.loads(raw)}
    except (TypeError, ValueError):
        logger.warning("Invalid queued input payload %s.", payload.get("id"))
        return None


async def discard_input(bus: "MessageBus", payload: dict) -> None:
    """Best-effort cleanup after the queue mutation has committed."""
    if "input_ref" not in payload:
        return
    try:
        await bus.registry_del(
            MessageBusKeys.chat_input_payloads(payload["session_id"]),
            payload["input_ref"],
        )
    except Exception:
        logger.exception("Could not discard queued input %s.", payload["id"])


async def cleanup_session(bus: "MessageBus", session_id: str) -> None:
    """Clean empty queues; retain indexes until their receipts are gone."""
    if await bus.queue_read(
        MessageBusKeys.chat_inputs(session_id),
        max_count=1,
    ):
        return
    if await bus.registry_exists(
        MessageBusKeys.chat_input_inflight_registry(),
        session_id,
    ):
        return
    await bus.registry_drop(MessageBusKeys.chat_input_payloads(session_id))
    await bus.registry_drop(MessageBusKeys.chat_input_claim(session_id))
    # Empty queues still need recovery while their receipts are retained.
    if await bus.registry_getall(
        MessageBusKeys.chat_submissions(session_id),
    ) or await bus.registry_getall(
        MessageBusKeys.chat_input_cancellations(session_id),
    ):
        return
    raw = await bus.registry_get(
        MessageBusKeys.chat_input_pending_registry(),
        session_id,
    )
    if raw is not None:
        try:
            user_id = json.loads(raw)["user_id"]
            if not isinstance(user_id, str) or not user_id:
                raise ValueError("Invalid pending-input owner.")
        except (KeyError, TypeError, ValueError):
            logger.warning("Invalid pending-input owner for %s.", session_id)
        else:
            await bus.registry_del(
                MessageBusKeys.chat_input_user_sessions(user_id),
                session_id,
            )
    await bus.registry_del(
        MessageBusKeys.chat_input_pending_registry(),
        session_id,
    )
