# -*- coding: utf-8 -*-
"""Durable FIFO claims, bounded retries, recovery and session wakeups."""
import asyncio
import json
import time
import uuid
from typing import Any, Coroutine, TYPE_CHECKING

from pydantic import TypeAdapter

from ..._logging import logger
from ...event import (
    CustomEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ...message import Msg
from ..message_bus import MessageBusKeys
from ._chat_run_registry import CHAT_RUN_SHUTDOWN
from .._bus_ops import (
    ChatQueueBusyError,
    chat_input_mutation,
    enqueue_run_trigger,
    publish_session_event,
)
from .._chat_input_store import (
    cleanup_session,
    discard_input,
    hydrate_input,
    queue_entry,
)

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import StorageBase
    from .._service import ChatService

_MESSAGE_INPUT_ADAPTER: TypeAdapter = TypeAdapter(Msg | list[Msg])
_MESSAGE_FALLBACK_TICK_SECS = 30.0
_MESSAGE_RECOVERY_LOCK_TTL_SECS = 60
_MESSAGE_RECOVERY_OWNER_FIELD = "owner"
_MESSAGE_RECOVERY_EXPIRES_FIELD = "expires_at"
_MESSAGE_CLAIMED = "claimed"
_MESSAGE_INPUT_PERSISTED = "input_persisted"
_MESSAGE_CANCELLED = "cancelled"
_MESSAGE_FAILED = "failed"
_MESSAGE_COMPLETED = "completed"


class ChatInputQueue:
    """Own the lifecycle of queued input independently from run dispatch."""

    def __init__(
        self,
        bus: "MessageBus",
        storage: "StorageBase",
        chat_service: "ChatService",
    ) -> None:
        self._bus = bus
        self._storage = storage
        self._chat_service = chat_service
        self._recovery_task: asyncio.Task | None = None
        self._recovery_owner = uuid.uuid4().hex
        self._retry_tasks: set[asyncio.Task] = set()
        self._closed = False

    async def close(self) -> None:
        """Cancel retained timers and release recovery leadership."""
        self._closed = True
        tasks = list(self._retry_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._retry_tasks.clear()
        self._recovery_task = None
        await self._release_recovery_leadership()

    def schedule_recovery(self) -> None:
        """Retain at most one non-blocking recovery sweep per process."""
        if self._recovery_task is not None and not self._recovery_task.done():
            return
        task = asyncio.create_task(
            self._recover_pending_chat_inputs(),
            name="wakeup-dispatcher:recovery",
        )
        self._recovery_task = task
        self._retry_tasks.add(task)

        def _done(completed: asyncio.Task) -> None:
            self._retry_tasks.discard(completed)
            if self._recovery_task is completed:
                self._recovery_task = None

        task.add_done_callback(_done)

    async def _recover_pending_chat_inputs(self) -> None:
        """Recreate message triggers for durably indexed non-empty FIFOs.

        A cluster-wide renewable leader lease lets only one API process
        perform the O(N) sweeps. Queue probes do not take the foreground
        mutation lock; an empty marker is removed only after a locked
        recheck.
        """
        pushed_count = 0
        try:
            recovery_lock = MessageBusKeys.chat_input_recovery_lock()
            async with self._bus.acquire_lock_bounded(
                recovery_lock,
                ttl_secs=_MESSAGE_RECOVERY_LOCK_TTL_SECS,
                timeout_secs=0.1,
            ):
                now = time.time()
                recovery_state = await self._bus.registry_getall(
                    MessageBusKeys.chat_input_recovery_state(),
                )
                try:
                    expires_at = float(
                        recovery_state.get(
                            _MESSAGE_RECOVERY_EXPIRES_FIELD,
                            "0",
                        ),
                    )
                except (TypeError, ValueError):
                    expires_at = 0.0
                owner = recovery_state.get(_MESSAGE_RECOVERY_OWNER_FIELD)
                if owner != self._recovery_owner and expires_at > now:
                    return
                await self._bus.registry_set(
                    MessageBusKeys.chat_input_recovery_state(),
                    _MESSAGE_RECOVERY_OWNER_FIELD,
                    self._recovery_owner,
                    ttl_secs=_MESSAGE_RECOVERY_LOCK_TTL_SECS * 2,
                )
                # Owner and expiry are intentionally separate writes. If the
                # leader dies between them, readers treat missing expiry as
                # zero and immediately allow another process to take over.
                await self._bus.registry_set(
                    MessageBusKeys.chat_input_recovery_state(),
                    _MESSAGE_RECOVERY_EXPIRES_FIELD,
                    str(now + _MESSAGE_FALLBACK_TICK_SECS * 2),
                    ttl_secs=_MESSAGE_RECOVERY_LOCK_TTL_SECS * 2,
                )
                pushed_count = await self._recover_pending_chat_inputs_locked()
        except TimeoutError:
            return
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "WakeupDispatcher: pending chat queue recovery failed.",
            )
        if pushed_count:
            # Recovery only creates durable triggers. One batched signal
            # wakes the single ``_loop`` consumer; non-leaders and empty
            # sweeps do not generate redundant pub/sub fan-out.
            try:
                await self._bus.publish(MessageBusKeys.wakeup_signal(), {})
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: failed to signal after recovery.",
                )

    async def _release_recovery_leadership(self) -> None:
        """Release this process's recovery lease during graceful shutdown."""
        try:
            recovery_lock = MessageBusKeys.chat_input_recovery_lock()
            async with self._bus.acquire_lock(
                recovery_lock,
                ttl_secs=_MESSAGE_RECOVERY_LOCK_TTL_SECS,
            ):
                recovery_state = await self._bus.registry_getall(
                    MessageBusKeys.chat_input_recovery_state(),
                )
                if (
                    recovery_state.get(_MESSAGE_RECOVERY_OWNER_FIELD)
                    != self._recovery_owner
                ):
                    return
                await self._bus.registry_del(
                    MessageBusKeys.chat_input_recovery_state(),
                    _MESSAGE_RECOVERY_OWNER_FIELD,
                )
                await self._bus.registry_del(
                    MessageBusKeys.chat_input_recovery_state(),
                    _MESSAGE_RECOVERY_EXPIRES_FIELD,
                )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "WakeupDispatcher: failed to release recovery leadership.",
            )

    async def _recover_pending_chat_inputs_locked(self) -> int:
        """Run one cluster-elected pending-input recovery sweep.

        Returns:
            `int`:
                Number of durable ``queued_message`` triggers pushed. The
                caller publishes one shared signal when this value is
                non-zero.
        """
        pushed_count = 0
        pending_sessions = await self._bus.registry_getall(
            MessageBusKeys.chat_input_pending_registry(),
        )
        inflight_sessions = await self._bus.registry_getall(
            MessageBusKeys.chat_input_inflight_registry(),
        )
        recovery_timeout = MessageBusKeys.CHAT_INPUT_RECOVERY_LOCK_TIMEOUT_SECS
        for session_id in pending_sessions.keys() | inflight_sessions.keys():
            try:
                raw_routing = pending_sessions.get(session_id)
                if raw_routing is not None:
                    routing = json.loads(raw_routing)
                else:
                    routing = json.loads(inflight_sessions[session_id])
                user_id = routing["user_id"]
                agent_id = routing["agent_id"]
                if not isinstance(user_id, str) or not isinstance(
                    agent_id,
                    str,
                ):
                    raise TypeError(
                        "Pending-chat routing ids must be strings.",
                    )
                pending = await self._bus.queue_read(
                    MessageBusKeys.chat_inputs(session_id),
                    max_count=1,
                )
                claimed = session_id in inflight_sessions
                if not pending and not claimed:
                    # Enqueue can register before pushing. Recheck under the
                    # same mutation lock before collecting a stale marker.
                    try:
                        async with chat_input_mutation(
                            self._bus,
                            session_id,
                            timeout_secs=recovery_timeout,
                        ):
                            pending = await self._bus.queue_read(
                                MessageBusKeys.chat_inputs(session_id),
                                max_count=1,
                            )
                            claimed = await self._bus.registry_exists(
                                MessageBusKeys.chat_input_inflight_registry(),
                                session_id,
                            )
                            if not pending and not claimed:
                                await cleanup_session(self._bus, session_id)
                    except ChatQueueBusyError:
                        # Never hold the cluster recovery lease while waiting
                        # behind a foreground enqueue/edit operation.
                        continue
                if not pending and not claimed:
                    continue
                # Reuse the canonical trigger serializer but batch the
                # signal. The sole ``_loop`` drainer consumes it after the
                # recovery method publishes once for the whole sweep.
                await enqueue_run_trigger(
                    self._bus,
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
                    signal=False,
                )
                pushed_count += 1
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: invalid pending-chat registry entry "
                    "for session %s.",
                    session_id,
                )
        return pushed_count

    async def _is_session_parked(
        self,
        session: object,
        user_id: str,
        agent_id: str,
    ) -> bool:
        """Use the owning agent's canonical pending-tool-call semantics."""
        state = getattr(session, "state", None)
        if not state or not state.context:
            return False
        record = await self._chat_service.resolve_agent(user_id, agent_id)
        return bool(
            state.get_awaiting_tool_calls(record.data.name),
        )

    async def run_resume_serialized(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | UserInterruptEvent
        | None,
    ) -> None:
        """Serialize a resume with chat-input claim and parked-state checks.

        Args:
            user_id (`str`):
                User that owns the session.
            session_id (`str`):
                Session whose parked run should resume.
            agent_id (`str`):
                Agent that owns the session.
            input_msg:
                Typed HITL/external-result/interrupt event, or ``None`` for
                a compatible control continuation.
        """
        dispatch_lock = self._bus.acquire_lock_bounded(
            MessageBusKeys.chat_input_dispatch_lock(session_id),
            ttl_secs=MessageBusKeys.CHAT_INPUT_DISPATCH_TTL_SECS,
            timeout_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TIMEOUT_SECS,
        )
        try:
            # pylint: disable-next=unnecessary-dunder-call
            await dispatch_lock.__aenter__()
        except TimeoutError:
            await enqueue_run_trigger(
                self._bus,
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                kind=MessageBusKeys.WAKEUP_KIND_RESUME,
                inputs=input_msg,
            )
            return
        try:
            await self._chat_service.run(
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                input_msg=input_msg,
            )
        finally:
            await dispatch_lock.__aexit__(None, None, None)

    async def drain_chat_inputs(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> bool:
        """Run at most one queued ordinary input for one session.

        A durable per-session claim is written before destructive dequeue.
        The input messages are then persisted before execution begins. A
        crash before persistence safely retries the claim; a crash after
        persistence publishes a terminal failure instead of replaying agent
        tools with unknown side effects.

        The long dispatch lease still surrounds generation, but contenders
        use a bounded acquisition wait and leave the durable claim/queue for
        a later nudge rather than blocking a process task indefinitely.

        Args:
            user_id (`str`):
                User that must own the queued turn.
            session_id (`str`):
                Session whose FIFO head may be executed.
            agent_id (`str`):
                Agent that must own the queued turn.

        Returns:
            `bool`:
                ``True`` when one valid claim reached a terminal state and
                the successor should be nudged; ``False`` when there was no
                work or execution was deferred.
        """
        dispatch_lock = self._bus.acquire_lock_bounded(
            MessageBusKeys.chat_input_dispatch_lock(session_id),
            ttl_secs=MessageBusKeys.CHAT_INPUT_DISPATCH_TTL_SECS,
            timeout_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TIMEOUT_SECS,
        )
        try:
            # pylint: disable-next=unnecessary-dunder-call
            await dispatch_lock.__aenter__()
        except TimeoutError:
            return False
        try:
            return await self._drain_chat_inputs_locked(
                user_id,
                session_id,
                agent_id,
            )
        finally:
            await dispatch_lock.__aexit__(None, None, None)

    async def _drain_chat_inputs_locked(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> bool:
        """Consume one valid claim while holding the dispatch mutex.

        Malformed claims are acknowledged and skipped until the FIFO is
        empty or one valid claim reaches a terminal state.

        Args:
            user_id (`str`):
                User expected to own the claim.
            session_id (`str`):
                Session whose dispatch mutex is already held.
            agent_id (`str`):
                Agent expected to own the claim.

        Returns:
            `bool`:
                ``True`` after consuming one valid/terminal claim;
                ``False`` when empty, busy, running, or HITL-parked.
        """
        while True:
            if await self._bus.is_locked(
                MessageBusKeys.session_lock(session_id),
            ):
                return False
            session = await self._storage.get_session(
                user_id,
                agent_id,
                session_id,
            )
            if session is None or await self._is_session_parked(
                session,
                user_id,
                agent_id,
            ):
                return False

            try:
                claim = await self._claim_chat_input(session_id)
            except ChatQueueBusyError:
                return False
            if claim is None:
                return False
            payload, claim_state = claim
            if claim_state == _MESSAGE_COMPLETED:
                await self._acknowledge_completed(session_id, payload)
                return True
            try:
                queued_user_id = payload["user_id"]
                queued_session_id = payload["session_id"]
                queued_agent_id = payload["agent_id"]
                queued_input = _MESSAGE_INPUT_ADAPTER.validate_python(
                    payload["input"],
                )
                messages = (
                    queued_input
                    if isinstance(queued_input, list)
                    else [queued_input]
                )
                if not messages:
                    raise ValueError("Queued input list must not be empty.")
                if (
                    queued_user_id != user_id
                    or queued_session_id != session_id
                    or queued_agent_id != agent_id
                ):
                    raise ValueError(
                        "Queued input ownership does not match its trigger.",
                    )
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: dropping malformed queued chat "
                    "claim for session %s.",
                    session_id,
                )
                await publish_session_event(
                    self._bus,
                    session_id,
                    CustomEvent(
                        name="chat_input_failed",
                        value={
                            "queue_item_id": payload.get("id"),
                            "message_ids": [],
                            "message": (
                                "The queued input is invalid or missing."
                            ),
                        },
                    ).model_dump(mode="json"),
                )
                await self._finish_chat_input_claim(session_id)
                continue

            queue_item_id = payload.get("id")
            if not isinstance(queue_item_id, str) or not queue_item_id:
                queue_item_id = messages[0].id
            message_ids = [msg.id for msg in messages]
            try:
                if claim_state == _MESSAGE_CANCELLED:
                    await self._publish_chat_input_cancelled(
                        queued_session_id,
                        queue_item_id,
                        message_ids,
                    )
                    await self._finish_chat_input_claim(session_id)
                    return True
                if claim_state in (_MESSAGE_INPUT_PERSISTED, _MESSAGE_FAILED):
                    failed_event = CustomEvent(
                        name="chat_input_failed",
                        value={
                            "queue_item_id": queue_item_id,
                            "message_ids": message_ids,
                            "message": (
                                "The queued message could not be saved after "
                                "repeated attempts. Check the server log."
                                if claim_state == _MESSAGE_FAILED
                                else "Processing stopped after the input was "
                                "saved. It was not replayed automatically "
                                "to avoid duplicate side effects."
                            ),
                        },
                    )
                    await publish_session_event(
                        self._bus,
                        queued_session_id,
                        failed_event.model_dump(mode="json"),
                    )
                    await self._finish_chat_input_claim(session_id)
                    return True

                for message in messages:
                    await self._storage.upsert_message(
                        queued_user_id,
                        queued_session_id,
                        message,
                    )
                await self._set_chat_input_claim_state(
                    session_id,
                    payload,
                    _MESSAGE_INPUT_PERSISTED,
                )
                claim_state = _MESSAGE_INPUT_PERSISTED
                started_event = CustomEvent(
                    name="chat_input_started",
                    value={
                        "queue_item_id": queue_item_id,
                        "message_ids": message_ids,
                        "queue_item": {
                            "id": queue_item_id,
                            "created_at": payload.get(
                                "created_at",
                                messages[0].created_at,
                            ),
                            "input": payload["input"],
                        },
                    },
                )
                await publish_session_event(
                    self._bus,
                    queued_session_id,
                    started_event.model_dump(mode="json"),
                )
                await self._chat_service.run(
                    user_id=queued_user_id,
                    session_id=queued_session_id,
                    agent_id=queued_agent_id,
                    input_msg=queued_input,
                    queue_item_id=queue_item_id,
                    input_persisted=True,
                )
                claim_state = _MESSAGE_COMPLETED
                await self._acknowledge_completed(session_id, payload)
                return True
            except asyncio.CancelledError as exc:
                if claim_state == _MESSAGE_COMPLETED or (
                    claim_state == _MESSAGE_CLAIMED
                    and exc.args == (CHAT_RUN_SHUTDOWN,)
                ):
                    raise
                cleanup_task = asyncio.create_task(
                    self._cancel_chat_input_claim(
                        session_id,
                        queued_session_id,
                        queue_item_id,
                        message_ids,
                        payload,
                    ),
                    name=f"cancel-chat-claim:{session_id}",
                )
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    try:
                        await cleanup_task
                    except Exception:  # pylint: disable=broad-except
                        logger.exception(
                            "WakeupDispatcher: failed to finish cancelled "
                            "queued input for session %s.",
                            queued_session_id,
                        )
                except Exception:  # pylint: disable=broad-except
                    logger.exception(
                        "WakeupDispatcher: failed to finish cancelled queued "
                        "input for session %s.",
                        queued_session_id,
                    )
                raise
            except Exception:
                logger.exception(
                    "Queued input %s failed in state %s.",
                    queue_item_id,
                    claim_state,
                )
                if claim_state == _MESSAGE_CLAIMED:
                    delay = MessageBusKeys.CHAT_INPUT_RETRY_SECS * payload.get(
                        "attempts",
                        1,
                    )
                    payload["retry_at"] = time.time() + delay
                    await self._set_chat_input_claim_state(
                        session_id,
                        payload,
                        _MESSAGE_CLAIMED,
                    )
                self.schedule_message_nudge(user_id, session_id, agent_id)
                return False

    async def _claim_chat_input(
        self,
        session_id: str,
    ) -> tuple[dict, str] | None:
        """Durably claim the FIFO head before destructively removing it.

        Args:
            session_id (`str`):
                Session whose FIFO head should be claimed.

        Returns:
            `tuple[dict, str] | None`:
                Raw queue payload and its durable claim state, or ``None``
                when neither an existing claim nor a pending item exists.

        Raises:
            `ChatQueueBusyError`:
                The session mutation lock cannot be acquired promptly.
        """
        async with chat_input_mutation(self._bus, session_id):
            raw = await self._bus.registry_get(
                MessageBusKeys.chat_input_claim(session_id),
                "claim",
            )
            claim = None
            if raw is not None:
                try:
                    claim = json.loads(raw)
                    if not isinstance(claim["payload"], dict) or claim[
                        "state"
                    ] not in (
                        _MESSAGE_CLAIMED,
                        _MESSAGE_INPUT_PERSISTED,
                        _MESSAGE_CANCELLED,
                        _MESSAGE_FAILED,
                        _MESSAGE_COMPLETED,
                    ):
                        raise ValueError("Invalid chat-input claim.")
                except (KeyError, TypeError, ValueError):
                    logger.exception(
                        "Deleting malformed claim for session %s.",
                        session_id,
                    )
                    claim = None
                    await self._bus.registry_drop(
                        MessageBusKeys.chat_input_claim(session_id),
                    )
            pending = await self._bus.queue_read(
                MessageBusKeys.chat_inputs(session_id),
                max_count=1,
            )
            if claim is None:
                if not pending:
                    await self._bus.registry_del(
                        MessageBusKeys.chat_input_inflight_registry(),
                        session_id,
                    )
                    await cleanup_session(self._bus, session_id)
                    return None
                payload = pending[0][1]
                claim = {"state": _MESSAGE_CLAIMED, "payload": payload}
            payload = claim["payload"]
            if payload.get("retry_at", 0) > time.time():
                return None
            if claim["state"] == _MESSAGE_CLAIMED:
                attempts = payload.get("attempts", 0)
                if attempts >= MessageBusKeys.CHAT_INPUT_MAX_ATTEMPTS:
                    claim["state"] = _MESSAGE_FAILED
                else:
                    payload["attempts"] = attempts + 1
            # Index first, local durable claim second, dequeue last. A crash
            # at any boundary leaves a discoverable item or harmless marker.
            await self._bus.registry_set(
                MessageBusKeys.chat_input_inflight_registry(),
                session_id,
                json.dumps(
                    {
                        key: payload.get(key)
                        for key in ("id", "user_id", "agent_id")
                    },
                ),
            )
            await self._bus.registry_set(
                MessageBusKeys.chat_input_claim(session_id),
                "claim",
                json.dumps(claim),
            )
            if pending and pending[0][1].get("id") == payload.get("id"):
                await self._bus.queue_drain(
                    MessageBusKeys.chat_inputs(session_id),
                    max_count=1,
                )
            if claim["state"] == _MESSAGE_COMPLETED:
                return payload, _MESSAGE_COMPLETED
            hydrated = await hydrate_input(self._bus, payload)
            return hydrated or payload, claim["state"]

    async def _set_chat_input_claim_state(
        self,
        session_id: str,
        payload: dict,
        state: str,
    ) -> None:
        """Persist a claim state transition under the mutation mutex.

        Internal claim transitions wait for the mutex without the short
        foreground queue timeout. Once an input has been claimed, abandoning
        this transition on ordinary UI contention could make recovery infer
        the wrong execution state.

        Args:
            session_id (`str`):
                Session that owns the in-flight claim.
            payload (`dict`):
                Raw claimed queue item retained for recovery.
            state (`str`):
                Durable state to store for the claim.

        """
        async with self._bus.acquire_lock(
            MessageBusKeys.chat_input_mutation_lock(session_id),
            ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
        ):
            await self._bus.registry_set(
                MessageBusKeys.chat_input_claim(session_id),
                "claim",
                json.dumps({"state": state, "payload": queue_entry(payload)}),
            )

    async def _acknowledge_completed(
        self,
        session_id: str,
        payload: dict,
    ) -> None:
        """Retry terminal bookkeeping without running or failing the turn.

        Keep the dispatch lease until completion is durable and acknowledged.
        Cancellation may leave a completed marker for recovery; a hard crash
        before that write remains an unknown outcome, never a safe replay.
        """
        marked = False
        while True:
            try:
                if not marked:
                    await self._set_chat_input_claim_state(
                        session_id,
                        payload,
                        _MESSAGE_COMPLETED,
                    )
                    marked = True
                await self._finish_chat_input_claim(session_id)
                return
            except Exception:
                logger.exception(
                    "Retrying completed claim acknowledgement for %s.",
                    session_id,
                )
                await asyncio.sleep(MessageBusKeys.CHAT_INPUT_RETRY_SECS)

    async def _finish_chat_input_claim(self, session_id: str) -> None:
        """Acknowledge one claim and garbage-collect an empty marker.

        This terminal acknowledgement uses the reliable internal mutation
        path rather than the bounded foreground path. The operations are
        idempotent, so callers may safely retry after an ambiguous bus error.

        Args:
            session_id (`str`):
                Session whose in-flight claim has reached a terminal state.

        """
        async with self._bus.acquire_lock(
            MessageBusKeys.chat_input_mutation_lock(session_id),
            ttl_secs=MessageBusKeys.CHAT_INPUT_MUTATION_TTL_SECS,
        ):
            raw = await self._bus.registry_get(
                MessageBusKeys.chat_input_claim(session_id),
                "claim",
            )
            await self._bus.registry_drop(
                MessageBusKeys.chat_input_claim(session_id),
            )
            await self._bus.registry_del(
                MessageBusKeys.chat_input_inflight_registry(),
                session_id,
            )
            if raw is not None:
                try:
                    payload = json.loads(raw)["payload"]
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid terminal claim payload.")
                except (KeyError, TypeError, ValueError):
                    logger.warning(
                        "Skipping malformed terminal claim data for %s.",
                        session_id,
                    )
                else:
                    await discard_input(self._bus, payload)
            await cleanup_session(self._bus, session_id)

    async def _cancel_chat_input_claim(
        self,
        session_id: str,
        event_session_id: str,
        queue_item_id: str,
        message_ids: list[str],
        payload: dict,
    ) -> None:
        """Acknowledge an explicit cancellation and publish its outcome.

        Args:
            session_id (`str`):
                Session that owns the durable claim.
            event_session_id (`str`):
                Session stream that receives the terminal event.
            queue_item_id (`str`):
                Stable id of the cancelled queue item.
            message_ids (`list[str]`):
                Message ids contained in the cancelled turn.
            payload (`dict`):
                Raw claimed payload retained until acknowledgement.
        """
        await self._set_chat_input_claim_state(
            session_id,
            payload,
            _MESSAGE_CANCELLED,
        )
        await self._publish_chat_input_cancelled(
            event_session_id,
            queue_item_id,
            message_ids,
        )
        await self._finish_chat_input_claim(session_id)

    async def _publish_chat_input_cancelled(
        self,
        event_session_id: str,
        queue_item_id: str,
        message_ids: list[str],
    ) -> None:
        """Publish a replayable terminal event for a cancelled claim.

        Args:
            event_session_id (`str`):
                Session stream that receives the event.
            queue_item_id (`str`):
                Stable id of the cancelled queue item.
            message_ids (`list[str]`):
                Message ids contained in the cancelled turn.
        """
        cancelled_event = CustomEvent(
            name="chat_input_cancelled",
            value={
                "queue_item_id": queue_item_id,
                "message_ids": message_ids,
                "message": "Queued message processing was interrupted.",
            },
        )
        await publish_session_event(
            self._bus,
            event_session_id,
            cancelled_event.model_dump(mode="json"),
        )

    def message_pump_done(
        self,
        task: asyncio.Task,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Nudge a successor only after a pump consumed a turn.

        Args:
            task (`asyncio.Task`):
                Completed queue-pump task whose result controls progression.
            user_id (`str`):
                User that owns the session queue.
            session_id (`str`):
                Session whose next item may be ready.
            agent_id (`str`):
                Agent that owns the session queue.
        """
        should_nudge = task.cancelled()
        if not should_nudge:
            try:
                should_nudge = task.result() is True
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: chat-input pump failed for "
                    "session %s.",
                    session_id,
                )
                should_nudge = True
        if not should_nudge:
            return
        self.schedule_message_nudge(user_id, session_id, agent_id)

    def schedule_message_nudge(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Hold a one-shot fast-path nudge task until it completes.

        Args:
            user_id (`str`):
                User that owns the session queue.
            session_id (`str`):
                Session whose pending/in-flight state should be checked.
            agent_id (`str`):
                Agent that owns the session queue.
        """

        if self._closed:
            return

        async def _nudge() -> None:
            try:
                raw = await self._bus.registry_get(
                    MessageBusKeys.chat_input_claim(session_id),
                    "claim",
                )
                if raw is not None:
                    retry_at = json.loads(raw)["payload"].get("retry_at", 0)
                    await asyncio.sleep(
                        max(
                            MessageBusKeys.CHAT_INPUT_RETRY_SECS,
                            retry_at - time.time(),
                        ),
                    )
                pending = await self._bus.queue_read(
                    MessageBusKeys.chat_inputs(session_id),
                    max_count=1,
                )
                claimed = await self._bus.registry_exists(
                    MessageBusKeys.chat_input_inflight_registry(),
                    session_id,
                )
                if pending or claimed:
                    await enqueue_run_trigger(
                        self._bus,
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                        kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
                    )
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: failed to nudge the next queued "
                    "input for session %s.",
                    session_id,
                )

        self._track_message_task(
            _nudge(),
            name=f"message-nudge:{session_id}",
        )

    def _track_message_task(
        self,
        coro: Coroutine[Any, Any, object],
        *,
        name: str,
    ) -> None:
        """Create and strongly retain one message-queue helper task.

        Args:
            coro (`Coroutine[Any, Any, object]`):
                Coroutine to schedule.
            name (`str`):
                Diagnostic asyncio task name.
        """
        task = asyncio.create_task(coro, name=name)
        self._retry_tasks.add(task)
        task.add_done_callback(self._retry_tasks.discard)
