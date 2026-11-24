# -*- coding: utf-8 -*-
"""Bounded submission leases and cancellation receipt retention."""

import asyncio
import json
from time import time
from typing import Awaitable, Callable, TYPE_CHECKING

from .._logging import logger
from .message_bus import MessageBusKeys

if TYPE_CHECKING:
    from .message_bus import MessageBus


async def track_receipts(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
) -> None:
    """Clear any idle deadline before creating new work or receipts."""
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


def waiting_for_receipt_expiry(routing: dict) -> bool:
    """Legacy routes and receipts needing execution evidence are due now."""
    deadline = routing.get("receipt_check_at")
    return isinstance(deadline, (int, float)) and deadline > time()


async def index_idle_session(
    bus: "MessageBus",
    session_id: str,
    records: tuple[dict[str, dict], dict[str, dict]] | None = None,
) -> None:
    """Publish idle quota usage before deferring recovery to its deadline."""
    pending = MessageBusKeys.chat_input_pending_registry()
    raw = await bus.registry_get(pending, session_id)
    if raw is None:
        return
    if records is None:
        records = (
            await _read_records(
                bus,
                MessageBusKeys.chat_submissions(session_id),
            ),
            await _read_records(
                bus,
                MessageBusKeys.chat_input_cancellations(session_id),
            ),
        )
    submissions, cancellations = records
    slots = _receipt_slots(submissions, set(cancellations))
    try:
        routing = json.loads(raw)
        user_id = routing["user_id"]
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("Invalid pending-input owner.")
    except (KeyError, TypeError, ValueError):
        logger.warning("Invalid pending-input owner for %s.", session_id)
        if not slots:
            await bus.registry_del(pending, session_id)
        return

    counts = MessageBusKeys.chat_idle_receipt_counts(user_id)
    if slots:
        if await bus.registry_get(counts, session_id) != str(slots):
            await bus.registry_set(counts, session_id, str(slots))
    else:
        await bus.registry_del(counts, session_id)
    await bus.registry_del(
        MessageBusKeys.chat_input_user_sessions(user_id),
        session_id,
    )
    if not slots:
        await bus.registry_del(pending, session_id)
        return

    # Claimed submissions without terminal proof must be polled next sweep.
    # A partial queued POST is checked when its reservation expires.
    check_at = min(
        _receipt_deadline(record)
        for registry in records
        for record in registry.values()
    )
    if routing.get("receipt_check_at", 0) != check_at:
        routing["receipt_check_at"] = check_at
        # Session deletion can purge the route while collection is running.
        try:
            await bus.registry_set_if(
                pending,
                session_id,
                json.dumps(routing),
                expected=raw,
            )
        except NotImplementedError:
            # Buses without compare-and-set retain their periodic sweep.
            pass


def _receipt_deadline(record: dict) -> float:
    if record.get("state") == "reserved":
        return record.get("expires_at", 0)
    if record.get("state") == "queued" and "retire_at" not in record:
        return record.get("expires_at", 0)
    return record.get("retire_at", 0)


async def _read_records(bus: "MessageBus", namespace: str) -> dict[str, dict]:
    records = await bus.registry_getall(namespace)
    return {key: json.loads(value) for key, value in records.items()}


def _receipt_slots(submissions: dict[str, dict], ids: set[str]) -> int:
    """Include capacity reserved for a future cancellation or partial POST."""
    reserved = 0
    for record in submissions.values():
        if record.get("state") == "reserved":
            reserved += 1
        elif record.get("state") == "queued":
            ids.add(record["queue_item_id"])
    return len(submissions) + len(ids) + reserved


async def receipt_count(
    bus: "MessageBus",
    session_id: str,
    queued_ids: set[str] | None = None,
) -> int:
    """Pending/claimed queue identities reserve their eventual receipt slot."""
    if queued_ids is None:
        pending = await bus.queue_read(MessageBusKeys.chat_inputs(session_id))
        queued_ids = {p["id"] for _, p in pending}
    raw = await bus.registry_get(
        MessageBusKeys.chat_input_claim(session_id),
        "claim",
    )
    if raw:
        queued_ids = queued_ids | {json.loads(raw)["payload"]["id"]}
    submissions = await _read_records(
        bus,
        MessageBusKeys.chat_submissions(session_id),
    )
    cancellations = await bus.registry_getall(
        MessageBusKeys.chat_input_cancellations(session_id),
    )
    return _receipt_slots(submissions, queued_ids.union(cancellations))


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
) -> None:
    """Collect terminal receipts and update idle queue quota usage.

    Never expire a cancellation fence while its queue entry/claim exists.
    A claimed turn can remain parked indefinitely; durable input results,
    rather than age or missing SSE, decide when its retention can begin.
    """
    now = time()
    deferred = []
    submissions_key = MessageBusKeys.chat_submissions(session_id)
    cancellations_key = MessageBusKeys.chat_input_cancellations(session_id)
    submissions = await _read_records(bus, submissions_key)
    cancellations = await _read_records(bus, cancellations_key)
    for namespace, records in (
        (submissions_key, submissions),
        (cancellations_key, cancellations),
    ):
        for key, record in list(records.items()):
            if record.get("queue_item_id", key) in active:
                continue
            state = record.get("state")
            if state == "reserved":
                if record["expires_at"] <= now:
                    await bus.registry_del(namespace, key)
                    del records[key]
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
                del records[key]
    if not active and not await bus.registry_exists(
        MessageBusKeys.chat_input_inflight_registry(),
        session_id,
    ):
        # A failed enqueue can leave a payload without a FIFO. Retire its
        # indexes before potentially slow result lookups consume the budget.
        await bus.registry_drop(MessageBusKeys.chat_input_payloads(session_id))
        await index_idle_session(
            bus,
            session_id,
            (submissions, cancellations),
        )
    # Delete cheap expired records in both registries before querying storage.
    if not deferred:
        cursors.pop(session_id, None)
        return
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
