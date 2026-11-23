# -*- coding: utf-8 -*-
"""Bounded submission leases and cancellation receipt retention."""

import asyncio
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


async def receipt_count(
    bus: "MessageBus",
    session_id: str,
    queued_ids: set[str] | None = None,
) -> int:
    """Pending/claimed queue identities reserve their eventual receipt slot."""
    submissions = await bus.registry_getall(
        MessageBusKeys.chat_submissions(session_id),
    )
    cancellations = await bus.registry_getall(
        MessageBusKeys.chat_input_cancellations(session_id),
    )
    records = [json.loads(value) for value in submissions.values()]
    if queued_ids is None:
        pending = await bus.queue_read(MessageBusKeys.chat_inputs(session_id))
        queued_ids = {p["id"] for _, p in pending}
    ids = set(cancellations) | queued_ids
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
    cursors: dict[str, str],
    active: set[str],
) -> bool:
    """Collect terminal receipts and report whether any receipts remain.

    Never expire a cancellation fence while its queue entry/claim exists.
    A claimed turn can remain parked indefinitely; durable input results,
    rather than age or missing SSE, decide when its retention can begin.
    """
    now = time()
    retained = 0
    deferred = []
    for namespace in (
        MessageBusKeys.chat_submissions(session_id),
        MessageBusKeys.chat_input_cancellations(session_id),
    ):
        for key, value in (await bus.registry_getall(namespace)).items():
            retained += 1
            record = json.loads(value)
            if record.get("queue_item_id", key) in active:
                continue
            state = record.get("state")
            if state == "reserved":
                if record["expires_at"] <= now:
                    await bus.registry_del(namespace, key)
                    retained -= 1
                continue
            if state == "queued":
                # A failed enqueue can leave its reservation but no input.
                if record.get("expires_at", 0) > now:
                    continue
            if state == "claimed" and "retire_at" not in record:
                deferred.append((key, record))
                continue
            if "retire_at" not in record:
                record["retire_at"] = (
                    now + MessageBusKeys.CHAT_RECEIPT_RETENTION_SECS
                )
                await bus.registry_set(namespace, key, json.dumps(record))
            elif record["retire_at"] <= now:
                await bus.registry_del(namespace, key)
                retained -= 1
    # Delete cheap expired records in both registries before querying storage.
    if not deferred:
        cursors.pop(session_id, None)
        return bool(retained)
    cursor = cursors.get(session_id, "")
    deferred.sort(key=lambda item: (item[0] <= cursor, item[0]))
    for key, record in deferred:
        # Advance before awaiting so even an outer timeout makes progress.
        cursors[session_id] = key
        try:
            async with asyncio.timeout(
                MessageBusKeys.CHAT_RECEIPT_QUERY_TIMEOUT_SECS,
            ):
                message_ids = record.get("message_ids") or [None]
                if record.get("reply_id"):
                    # All inputs in this submission share one bound reply.
                    message_ids = message_ids[:1]
                all_finished = True
                for mid in message_ids:
                    result = await input_result(
                        user_id,
                        session_id,
                        agent_id,
                        mid,
                        **(
                            {"submission_id": key}
                            if "reply_id" in record
                            else {}
                        ),
                    )
                    if result["state"] != "finished":
                        all_finished = False
                        break
        except TimeoutError:
            continue
        if all_finished:
            record["retire_at"] = (
                now + MessageBusKeys.CHAT_RECEIPT_RETENTION_SECS
            )
            await bus.registry_set(
                MessageBusKeys.chat_submissions(session_id),
                key,
                json.dumps(record),
            )
    return True
