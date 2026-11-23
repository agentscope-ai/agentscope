# -*- coding: utf-8 -*-
"""Session-local payload storage and lightweight queue indexes."""
import json
from typing import TYPE_CHECKING

from .._logging import logger
from .message_bus import MessageBusKeys

if TYPE_CHECKING:
    from .message_bus import MessageBus


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
    """Remove empty queue indexes and payloads under the mutation lock."""
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
    await bus.registry_drop(MessageBusKeys.chat_input_payloads(session_id))
    await bus.registry_drop(MessageBusKeys.chat_input_claim(session_id))
    await bus.registry_del(
        MessageBusKeys.chat_input_pending_registry(),
        session_id,
    )
