# -*- coding: utf-8 -*-
"""Bounded submission leases and cancellation receipt retention."""
import json
from time import time
from typing import Awaitable, Callable, TYPE_CHECKING

from .message_bus import MessageBusKeys

if TYPE_CHECKING:
    from .message_bus import MessageBus


async def track_receipts(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
) -> None:
    """Reuse queue recovery/quota indexes before writing receipts."""
    await bus.registry_set(
        MessageBusKeys.chat_input_pending_registry(),
        session_id,
        json.dumps({"user_id": user_id, "agent_id": agent_id}),
    )
    await bus.registry_set(
        MessageBusKeys.chat_input_user_sessions(user_id),
        session_id,
        agent_id,
    )


async def receipt_count(bus: "MessageBus", session_id: str) -> int:
    """Pending/claimed queue identities reserve their eventual receipt slot."""
    submissions = await bus.registry_getall(
        MessageBusKeys.chat_submissions(session_id),
    )
    cancellations = await bus.registry_getall(
        MessageBusKeys.chat_input_cancellations(session_id),
    )
    records = [json.loads(value) for value in submissions.values()]
    pending = await bus.queue_read(MessageBusKeys.chat_inputs(session_id))
    ids = set(cancellations) | {p["id"] for _, p in pending}
    # A partial enqueue may still need a cancellation receipt without a FIFO
    # entry. Count its reserved identity until it is cancelled or collected.
    ids.update(r["queue_item_id"] for r in records if r["state"] == "queued")
    raw = await bus.registry_get(
        MessageBusKeys.chat_input_claim(session_id),
        "claim",
    )
    if raw:
        ids.add(json.loads(raw)["payload"]["id"])
    reserved = sum(r["state"] == "reserved" for r in records)
    return len(submissions) + len(ids) + reserved


async def receipt_capacity(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    slots: int = 1,
) -> bool:
    """Check admission while the caller holds the user's quota lock."""
    count = await receipt_count(bus, session_id) + slots
    if count > MessageBusKeys.CHAT_RECEIPT_SESSION_MAX_LEN:
        return False
    sessions = await bus.registry_getall(
        MessageBusKeys.chat_input_user_sessions(user_id),
    )
    for sid in sessions:
        if sid != session_id:
            count += await receipt_count(bus, sid)
        if count > MessageBusKeys.CHAT_RECEIPT_USER_MAX_LEN:
            return False
    return count <= MessageBusKeys.CHAT_RECEIPT_USER_MAX_LEN


def cancellation_receipt(user_id: str, agent_id: str) -> str:
    """Timestamp a receipt without refreshing its deadline on retries."""
    return json.dumps(
        {
            "user_id": user_id,
            "agent_id": agent_id,
            "retire_at": time() + MessageBusKeys.CHAT_RECEIPT_RETENTION_SECS,
        },
    )


async def reap_receipts(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
    input_result: Callable[..., Awaitable[dict]],
) -> None:
    """Collect only proven terminal receipts under the mutation lock.

    Never expire a cancellation fence while its queue entry/claim exists.
    A claimed turn can remain parked indefinitely; durable input results,
    rather than age or missing SSE, decide when its retention can begin.
    """
    now = time()
    pending = await bus.queue_read(MessageBusKeys.chat_inputs(session_id))
    active = {p["id"] for _, p in pending}
    raw = await bus.registry_get(
        MessageBusKeys.chat_input_claim(session_id),
        "claim",
    )
    if raw:
        active.add(json.loads(raw)["payload"]["id"])
    for namespace in (
        MessageBusKeys.chat_submissions(session_id),
        MessageBusKeys.chat_input_cancellations(session_id),
    ):
        for key, value in (await bus.registry_getall(namespace)).items():
            record = json.loads(value)
            if record.get("queue_item_id", key) in active:
                continue
            state = record.get("state")
            if state == "reserved":
                if record["expires_at"] <= now:
                    await bus.registry_del(namespace, key)
                continue
            if state == "queued":
                # A failed enqueue can leave its reservation but no input.
                if record.get("expires_at", 0) > now:
                    continue
            if state == "claimed" and "retire_at" not in record:
                # Older receipts lack input identities; require idle state.
                message_ids = record.get("message_ids") or [None]
                results = [
                    await input_result(user_id, session_id, agent_id, mid)
                    for mid in message_ids
                ]
                if any(r["state"] != "finished" for r in results):
                    continue
            if "retire_at" not in record:
                record["retire_at"] = (
                    now + MessageBusKeys.CHAT_RECEIPT_RETENTION_SECS
                )
                await bus.registry_set(namespace, key, json.dumps(record))
            elif record["retire_at"] <= now:
                await bus.registry_del(namespace, key)
