# -*- coding: utf-8 -*-
"""Single per-process dispatcher for all cross-session run triggers.

One asyncio task per process. Subscribes to the shared trigger signal
channel and drains the durable trigger queue on each signal. It is the
**sole** site that spawns :meth:`ChatService.run` into the shared
:class:`ChatRunRegistry`, which is what makes concurrent-spawn races
(two writers contending for one session's run slot → a spurious "already
has an active chat run" 409) structurally impossible: every run trigger
funnels through this one serial consumer.

Each queue entry carries a ``kind`` that selects how a busy session is
handled:

- ``wake`` (idle-session wake-up, ``input_msg=None``): produced only
  when no run is registered as the session's inbox consumer, so it
  always has something to deliver.
- ``resume`` (a parked HITL run being fed its result): carries the
  event the parked run is waiting for.

No kind is ever dropped while the session lock is held. Retries are
coalesced per ``(kind, session_id)``, use exponential backoff with a
max-attempt and deadline budget, and on exhaustion emit an explicit
session failure plus park the entry in a bounded dead-letter list so a
long-held lock cannot silently exhaust the message-bus connection pool
(see #2677).

All bus keys live on the :class:`MessageBus` base class (see
``enqueue_wakeup`` / ``enqueue_input``, ``dequeue_wakeups``,
``subscribe_wakeup_signal``, ``session_is_running``), so this file has
no hard-coded key strings.
"""
import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Self

from pydantic import TypeAdapter

from ..._logging import logger
from ...event import (
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ...message import Msg
from ...types import ErrorInfo, ErrorType, ReplyFinishedReason
from ..message_bus import MessageBusKeys
from .._bus_ops import enqueue_run_trigger, publish_session_event

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import StorageBase
    from .._service import ChatService
    from ._chat_run_registry import ChatRunRegistry

# Parses a queued ``resume`` input dict back into its concrete event,
# discriminated by the ``type`` field shared by these result events.
_RESUME_INPUT_ADAPTER: TypeAdapter = TypeAdapter(
    UserConfirmResultEvent | ExternalExecutionResultEvent | UserInterruptEvent,
)

# Base delay before re-queuing a trigger whose target session still
# holds its run lock. Combined with exponential growth + jitter so a
# long-held lock cannot spawn a Redis connection-pool storm.
_RESUME_RETRY_BACKOFF_SECS = 0.1
_RESUME_RETRY_BACKOFF_CAP_SECS = 2.0
_RESUME_RETRY_DEADLINE_SECS = 60.0
_RESUME_RETRY_MAX_ATTEMPTS = 20
_RESUME_RETRY_JITTER = 0.1
_RESUME_DEAD_LETTER_MAX = 128

TriggerInput = (
    UserConfirmResultEvent
    | ExternalExecutionResultEvent
    | UserInterruptEvent
    | Msg
    | None
)

RetryExhaustReason = Literal["deadline", "max_attempts"]


@dataclass
class _RetryBatch:
    """Buffered re-enqueue requests for one ``(kind, session_id)`` key."""

    user_id: str
    session_id: str
    agent_id: str
    kind: str
    trigger_id: str
    inputs: list[TriggerInput] = field(default_factory=list)
    attempt: int = 0
    retry_started_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class _DeadLetter:
    """Expired lock-held retry parked for visibility / later replay."""

    trigger_id: str
    user_id: str
    session_id: str
    agent_id: str
    kind: str
    attempt: int
    retry_started_at: float
    expired_at: float
    reason: RetryExhaustReason
    buffered_inputs: int


class WakeupDispatcher:
    """One asyncio task per process, draining the shared trigger queue.

    Args:
        message_bus (`MessageBus`):
            Application message bus. Used for signal subscription,
            queue drain, ``session_is_running`` checks, and re-queuing
            deferred ``resume`` triggers.
        storage (`StorageBase`):
            Persistent storage backend. Consulted before spawning a
            run so triggers whose target session has been deleted are
            dropped instead of crashing :class:`ChatService.run`.
        chat_service (`ChatService`):
            Drives the actual chat run when a trigger fires.
        chat_run_registry (`ChatRunRegistry`):
            Per-process registry that holds the spawned task handle so
            it can be located by :class:`CancelDispatcher`.
    """

    def __init__(
        self,
        message_bus: "MessageBus",
        storage: "StorageBase",
        chat_service: "ChatService",
        chat_run_registry: "ChatRunRegistry",
    ) -> None:
        """Bind dependencies.

        Args:
            message_bus (`MessageBus`):
                Application message bus.
            storage (`StorageBase`):
                Persistent storage backend.
            chat_service (`ChatService`):
                Drives session runs via :meth:`ChatService.run`.
            chat_run_registry (`ChatRunRegistry`):
                Shared chat-run registry to spawn into.
        """
        self._bus = message_bus
        self._storage = storage
        self._chat_service = chat_service
        self._registry = chat_run_registry
        self._task: asyncio.Task | None = None
        # One detached backoff timer per ``(kind, session_id)``. Held so
        # timers are not garbage-collected mid-sleep, can be cancelled on
        # shutdown, and cannot multiply into a Redis connection storm
        # while a session lock is held (see #2677).
        self._retry_tasks: dict[str, asyncio.Task] = {}
        self._retry_batches: dict[str, _RetryBatch] = {}
        # Bounded park for budget-exhausted retries so operators can see
        # (and later replay) what would otherwise vanish from Redis.
        self._dead_letters: deque[_DeadLetter] = deque(
            maxlen=_RESUME_DEAD_LETTER_MAX,
        )

    async def __aenter__(self) -> Self:
        """Start the dispatcher loop and wait until its bus
        subscription is live.

        Also performs an initial drain right after subscription so
        triggers produced while this process was down (durable in
        the queue) are picked up immediately on startup.

        Returns:
            `Self`: This dispatcher instance.
        """
        ready = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(ready),
            name="wakeup-dispatcher",
        )
        await ready.wait()
        await self._drain_and_dispatch()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Cancel the dispatcher loop and any pending retries."""
        retries = list(self._retry_tasks.values())
        for retry in retries:
            retry.cancel()
        for retry in retries:
            try:
                await retry
            except asyncio.CancelledError:
                pass
        self._retry_tasks.clear()
        self._retry_batches.clear()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _loop(self, ready: asyncio.Event) -> None:
        """Long-lived loop: subscribe to the signal channel and drain
        the queue on every received signal.

        Args:
            ready (`asyncio.Event`):
                Signalled after the underlying SUBSCRIBE completes.
                :meth:`start` blocks on this so callers can publish a
                trigger immediately after start without racing.
        """
        try:
            async for _signal in self._bus.subscribe(
                MessageBusKeys.wakeup_signal(),
                on_ready=ready.set,
            ):
                await self._drain_and_dispatch()
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "WakeupDispatcher loop crashed; subscription ended.",
            )

    async def _drain_and_dispatch(self) -> None:
        """Read up to a batch of trigger entries and dispatch each."""
        try:
            raw_entries = await self._bus.queue_drain(
                MessageBusKeys.wakeup_queue(),
                max_count=64,
            )
            entries = [payload for _, payload in raw_entries]
        except Exception:  # pylint: disable=broad-except
            logger.exception("WakeupDispatcher: dequeue_wakeups failed.")
            return

        for payload in entries:
            try:
                user_id = payload["user_id"]
                session_id = payload["session_id"]
                agent_id = payload["agent_id"]
            except (KeyError, TypeError):
                logger.warning(
                    "WakeupDispatcher: skipping malformed trigger entry %r",
                    payload,
                )
                continue
            # Entries from older producers omit ``kind`` — treat as wake.
            kind = payload.get("kind", MessageBusKeys.WAKEUP_KIND_WAKE)
            try:
                await self._dispatch_one(
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    kind=kind,
                    raw_input=payload.get("input"),
                    retry_attempt=int(payload.get("retry_attempt", 0) or 0),
                    retry_started_at=payload.get("retry_started_at"),
                )
            except Exception:  # pylint: disable=broad-except
                # A single bad entry or transient bus error must not tear
                # down the long-lived subscription loop (#2677).
                logger.exception(
                    "WakeupDispatcher: dispatch failed for session %s "
                    "(kind=%s); continuing.",
                    session_id,
                    kind,
                )

    async def _dispatch_one(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        kind: str,
        raw_input: dict | None,
        retry_attempt: int = 0,
        retry_started_at: float | None = None,
    ) -> None:
        """Dispatch a single trigger entry by its ``kind``.

        Args:
            user_id (`str`):
                The owning user id.
            session_id (`str`):
                The session to trigger.
            agent_id (`str`):
                The agent that owns the session.
            kind (`str`):
                Trigger kind (``wake`` / ``resume``); see module docstring.
            raw_input (`dict | None`):
                Serialised input event for ``resume`` triggers, else
                ``None``.
            retry_attempt (`int`):
                How many times this trigger has already been deferred
                because the session lock was held. Used for backoff and
                deadline checks.
            retry_started_at (`float | None`):
                Wall-clock time of the first deferral, carried on the
                queue so the deadline is not reset each re-queue.
        """
        is_resume = kind == MessageBusKeys.WAKEUP_KIND_RESUME
        is_message = kind == MessageBusKeys.WAKEUP_KIND_MESSAGE
        # ``resume`` and ``message`` both carry input that must be
        # delivered — never dropped while the session is busy.
        carries_input = is_resume or is_message

        # Parse the carried input early so every downstream path
        # (lock-retry, spawn-retry) receives a typed object rather than a
        # raw dict.
        input_msg: TriggerInput = None
        if carries_input:
            if raw_input is None:
                logger.warning(
                    "WakeupDispatcher: dropping %s trigger for session "
                    "%s — no input carried.",
                    kind,
                    session_id,
                )
                return
            try:
                input_msg = (
                    Msg.model_validate(raw_input)
                    if is_message
                    else _RESUME_INPUT_ADAPTER.validate_python(raw_input)
                )
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: dropping %s trigger for session "
                    "%s — input failed to parse: %r",
                    kind,
                    session_id,
                    raw_input,
                )
                return

        if await self._bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        ):
            # The session is busy, so nothing can be spawned right now:
            # re-queue after a short backoff rather than dropping.
            #
            # ``wake`` needs this as much as the input-carrying kinds. A
            # producer only enqueues one after finding no registered
            # inbox consumer, which a finished run gives up *before* it
            # releases the session lock — so a held lock is no evidence
            # that anyone is still going to drain the inbox.
            self._schedule_retry(
                user_id,
                session_id,
                agent_id,
                kind,
                input_msg,
                retry_attempt=retry_attempt,
                retry_started_at=retry_started_at,
            )
            return

        # Orphan guard: the queue is unaware of session lifecycle. A
        # trigger enqueued before the session was deleted (e.g. by a
        # BG-task completion callback or a schedule trigger) will still
        # arrive here. Drop it rather than letting ChatService.run crash
        # on a missing storage record.
        if (
            await self._storage.get_session(user_id, agent_id, session_id)
            is None
        ):
            logger.warning(
                "WakeupDispatcher: dropping %s trigger for session %s "
                "(agent %s, user %s) — session no longer exists in "
                "storage; it was likely enqueued before the session was "
                "deleted.",
                kind,
                session_id,
                agent_id,
                user_id,
            )
            # Surface an error on the event stream so collectors (e.g. the
            # channel gateway) fail fast instead of waiting for a timeout.
            await publish_session_event(
                self._bus,
                session_id,
                ReplyEndEvent(
                    session_id=session_id,
                    reply_id="",
                    finished_reason=ReplyFinishedReason.ERROR,
                    error=ErrorInfo(
                        type=ErrorType.INTERNAL,
                        message="Session no longer exists.",
                    ),
                ).model_dump(mode="json"),
            )
            return

        try:
            self._registry.spawn(
                self._chat_service.run(
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    input_msg=input_msg,
                ),
                session_id=session_id,
                name=f"{kind}-run:{session_id}",
            )
        except RuntimeError:
            # A local run was registered between the running-check and
            # the spawn. Re-queue so neither the carried input nor a
            # queued inbox payload is stranded — that run may already be
            # past its last drain.
            self._schedule_retry(
                user_id,
                session_id,
                agent_id,
                kind,
                input_msg,
                retry_attempt=retry_attempt,
                retry_started_at=retry_started_at,
            )

    @staticmethod
    def _retry_key(kind: str, session_id: str) -> str:
        """Stable key for coalescing deferred re-enqueues."""
        return f"{kind}:{session_id}"

    @staticmethod
    def _make_trigger_id(kind: str, session_id: str, started: float) -> str:
        """Stable id for one lock-held retry chain (session + kind + start)."""
        return f"{kind}:{session_id}:{int(started * 1000)}"

    @staticmethod
    def _retry_delay_secs(attempt: int) -> float:
        """Exponential backoff with jitter, capped for lock-held retries."""
        exp = min(max(attempt, 0), 6)
        delay = min(
            _RESUME_RETRY_BACKOFF_CAP_SECS,
            _RESUME_RETRY_BACKOFF_SECS * (2**exp),
        )
        jitter = 1.0 + random.uniform(
            -_RESUME_RETRY_JITTER,
            _RESUME_RETRY_JITTER,
        )
        return max(0.0, delay * jitter)

    @staticmethod
    def _budget_exhausted(
        batch: _RetryBatch,
        *,
        now: float | None = None,
    ) -> RetryExhaustReason | None:
        """Return why the retry budget is spent, or ``None`` if still open."""
        clock = time.time() if now is None else now
        if batch.attempt >= _RESUME_RETRY_MAX_ATTEMPTS:
            return "max_attempts"
        if clock - batch.retry_started_at >= _RESUME_RETRY_DEADLINE_SECS:
            return "deadline"
        return None

    def _park_dead_letter(
        self,
        batch: _RetryBatch,
        reason: RetryExhaustReason,
    ) -> _DeadLetter:
        """Append an expired batch to the bounded dead-letter deque."""
        letter = _DeadLetter(
            trigger_id=batch.trigger_id,
            user_id=batch.user_id,
            session_id=batch.session_id,
            agent_id=batch.agent_id,
            kind=batch.kind,
            attempt=batch.attempt,
            retry_started_at=batch.retry_started_at,
            expired_at=time.time(),
            reason=reason,
            buffered_inputs=len(batch.inputs),
        )
        self._dead_letters.append(letter)
        return letter

    async def _expire_retry_batch(
        self,
        batch: _RetryBatch,
        reason: RetryExhaustReason,
    ) -> None:
        """Fail loud and park an exhausted retry instead of silent drop."""
        elapsed = time.time() - batch.retry_started_at
        letter = self._park_dead_letter(batch, reason)
        buffered = letter.buffered_inputs
        batch.inputs.clear()
        message = (
            f"Wakeup retry budget exhausted "
            f"(reason={reason}, session_id={batch.session_id}, "
            f"trigger_id={batch.trigger_id}, kind={batch.kind}, "
            f"attempts={batch.attempt}, elapsed={elapsed:.1f}s, "
            f"buffered={buffered})."
        )
        logger.warning("WakeupDispatcher: %s", message)
        await publish_session_event(
            self._bus,
            batch.session_id,
            ReplyEndEvent(
                session_id=batch.session_id,
                reply_id="",
                finished_reason=ReplyFinishedReason.ERROR,
                error=ErrorInfo(
                    type=ErrorType.INTERNAL,
                    message=message,
                ),
            ).model_dump(mode="json"),
        )

    def _schedule_retry(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        kind: str,
        input_msg: TriggerInput,
        *,
        retry_attempt: int = 0,
        retry_started_at: float | None = None,
    ) -> None:
        """Re-enqueue a trigger after backoff, coalesced per session/kind.

        Spawns at most one detached timer per ``(kind, session_id)``. Extra
        lock-held arrivals while that timer is pending are buffered onto the
        same batch so Redis re-enqueue concurrency stays bounded (#2677).

        Args:
            user_id (`str`):
                The owning user id.
            session_id (`str`):
                The session to trigger.
            agent_id (`str`):
                The agent that owns the session.
            kind (`str`):
                The trigger kind to re-enqueue (``wake`` / ``resume`` /
                ``message``).
            input_msg:
                The parsed input to redeliver (``None`` for ``wake``).
            retry_attempt (`int`):
                Prior deferral count for backoff / deadline / max attempts.
            retry_started_at (`float | None`):
                Wall-clock start of this retry chain. ``None`` on the
                first deferral.
        """
        key = self._retry_key(kind, session_id)
        started = (
            retry_started_at
            if retry_started_at is not None
            else time.time()
        )
        batch = self._retry_batches.get(key)
        if batch is None:
            batch = _RetryBatch(
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                kind=kind,
                trigger_id=self._make_trigger_id(kind, session_id, started),
                attempt=retry_attempt,
                retry_started_at=started,
            )
            self._retry_batches[key] = batch
        else:
            batch.attempt = max(batch.attempt, retry_attempt)
            batch.retry_started_at = min(batch.retry_started_at, started)

        # ``wake`` carries no input — keep a single slot. Input-carrying
        # kinds buffer each payload so HITL / channel messages are not
        # dropped when coalescing timers.
        if kind == MessageBusKeys.WAKEUP_KIND_WAKE:
            batch.inputs = [None]
        else:
            batch.inputs.append(input_msg)

        reason = self._budget_exhausted(batch)
        if reason is not None:
            if key not in self._retry_tasks:
                # Expire immediately on the event loop without another sleep.
                task = asyncio.create_task(
                    self._expire_and_drop(key, batch, reason),
                    name=f"{kind}-expire:{session_id}",
                )
                self._retry_tasks[key] = task
            return

        if key in self._retry_tasks:
            return
        self._arm_retry(key, batch)

    async def _expire_and_drop(
        self,
        key: str,
        batch: _RetryBatch,
        reason: RetryExhaustReason,
    ) -> None:
        """Expire a batch and clear its bookkeeping entry."""
        try:
            await self._expire_retry_batch(batch, reason)
        finally:
            self._retry_tasks.pop(key, None)
            self._retry_batches.pop(key, None)

    def _arm_retry(self, key: str, batch: _RetryBatch) -> None:
        """Start the single backoff timer for ``batch`` if none is running."""
        if key in self._retry_tasks:
            return
        kind = batch.kind
        session_id = batch.session_id

        async def _retry() -> None:
            cancelled = False
            try:
                reason = self._budget_exhausted(batch)
                if reason is not None:
                    await self._expire_retry_batch(batch, reason)
                    return

                await asyncio.sleep(self._retry_delay_secs(batch.attempt))
                reason = self._budget_exhausted(batch)
                if reason is not None:
                    await self._expire_retry_batch(batch, reason)
                    return

                pending = list(batch.inputs)
                batch.inputs.clear()
                next_attempt = batch.attempt + 1
                if next_attempt >= _RESUME_RETRY_MAX_ATTEMPTS:
                    batch.attempt = next_attempt
                    # Restore buffered inputs so the dead-letter counts them.
                    batch.inputs.extend(pending)
                    await self._expire_retry_batch(batch, "max_attempts")
                    return

                for buffered in pending:
                    await enqueue_run_trigger(
                        self._bus,
                        user_id=batch.user_id,
                        session_id=batch.session_id,
                        agent_id=batch.agent_id,
                        kind=kind,  # type: ignore[arg-type]
                        inputs=buffered,
                        retry_attempt=next_attempt,
                        retry_started_at=batch.retry_started_at,
                    )
            except asyncio.CancelledError:
                cancelled = True
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: failed to re-enqueue %s trigger "
                    "for session %s.",
                    kind,
                    session_id,
                )
            finally:
                self._retry_tasks.pop(key, None)
                if cancelled or not batch.inputs:
                    self._retry_batches.pop(key, None)
                else:
                    self._arm_retry(key, batch)

        task = asyncio.create_task(
            _retry(),
            name=f"{kind}-retry:{session_id}",
        )
        self._retry_tasks[key] = task
