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

- ``wake`` (idle-session wake-up, ``input_msg=None``): delivers pending
  inbox content and is retried while the session is running.
- ``resume`` (a parked HITL run being fed its result): must *not* be
  skipped while running, because the session is typically still running
  the parked tail at trigger time. It is re-queued after a short backoff
  until the parked run releases its session lock, then spawned with the
  carried input event.
- ``message`` (an inbound-channel ``Msg``): carries a genuine user message
  and is retried until it can start a run.
- ``queued_message`` (ordinary browser input): drains a per-session FIFO
  only after the current reply is complete and the session is not parked
  on HITL. Durable recovery replaces per-session hot retries.

All bus keys live on the :class:`MessageBus` base class (see
``enqueue_wakeup`` / ``enqueue_input``, ``dequeue_wakeups``,
``subscribe_wakeup_signal``, ``session_is_running``), so this file has
no hard-coded key strings.
"""
import asyncio
import json
import time
import uuid
from typing import Any, TYPE_CHECKING, Coroutine, Self

from pydantic import TypeAdapter

from ..._logging import logger
from ...event import (
    CustomEvent,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ...message import Msg, ToolCallState
from ...types import ErrorInfo, ErrorType, ReplyFinishedReason
from ..message_bus import MessageBusKeys
from .._bus_ops import (
    ChatQueueBusyError,
    chat_input_mutation,
    enqueue_run_trigger,
    publish_session_event,
)

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
_MESSAGE_INPUT_ADAPTER: TypeAdapter = TypeAdapter(Msg | list[Msg])

# Delay before re-queuing a trigger whose target session still holds
# its run lock. Short enough to feel instant to the user, long enough
# to avoid a hot re-enqueue loop while the lock is held.
_RESUME_RETRY_BACKOFF_SECS = 0.1

# Durably indexed pending queues are revisited at this low frequency.
# This is a crash/lost-signal safety net, not a per-session hot poll.
_MESSAGE_FALLBACK_TICK_SECS = 30.0
_MESSAGE_RECOVERY_LOCK_TTL_SECS = 60
_MESSAGE_RECOVERY_OWNER_FIELD = "owner"
_MESSAGE_RECOVERY_EXPIRES_FIELD = "expires_at"
_MESSAGE_CLAIMED = "claimed"
_MESSAGE_INPUT_PERSISTED = "input_persisted"
_MESSAGE_CANCELLED = "cancelled"


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
        self._fallback_task: asyncio.Task | None = None
        self._recovery_task: asyncio.Task | None = None
        self._recovery_owner = uuid.uuid4().hex
        # Detached resume timers and one-shot message queue helpers. Held
        # so they are not garbage-collected mid-await and can be cancelled
        # on shutdown.
        self._retry_tasks: set[asyncio.Task] = set()

    async def __aenter__(self) -> Self:
        """Start the dispatcher loop and wait until its bus
        subscription is live.

        After subscribing, publishes a signal for durable triggers that
        predate process startup. The potentially O(N) pending-session
        recovery sweep runs in a retained background task so application
        startup is not blocked by the number of indexed sessions.

        Returns:
            `Self`: This dispatcher instance.
        """
        ready = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(ready),
            name="wakeup-dispatcher",
        )
        await ready.wait()
        self._fallback_task = asyncio.create_task(
            self._fallback_loop(),
            name="wakeup-dispatcher:fallback",
        )
        await self._bus.publish(MessageBusKeys.wakeup_signal(), {})
        self._schedule_recovery()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Cancel the dispatcher loop and any pending retries."""
        retries = list(self._retry_tasks)
        for retry in retries:
            retry.cancel()
        for retry in retries:
            try:
                await retry
            except asyncio.CancelledError:
                pass
        self._retry_tasks.clear()
        self._recovery_task = None
        for task in (self._fallback_task, self._task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._fallback_task = None
        self._task = None
        await self._release_recovery_leadership()

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

    async def _fallback_loop(self) -> None:
        """Periodically signal the sole drain loop and schedule recovery."""
        try:
            while True:
                await asyncio.sleep(_MESSAGE_FALLBACK_TICK_SECS)
                # Never drain here: Redis queue_drain is XRANGE + XDEL and
                # therefore requires a single in-process consumer. All
                # actual draining remains serialized in ``_loop``.
                await self._bus.publish(MessageBusKeys.wakeup_signal(), {})
                self._schedule_recovery()
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "WakeupDispatcher fallback loop crashed.",
            )

    def _schedule_recovery(self) -> None:
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
            # Avoid joining a blocking convoy behind another process. The
            # leader check inside the lock handles the small check/claim
            # race where two processes observe the lock as free.
            if await self._bus.is_locked(recovery_lock):
                return
            async with self._bus.acquire_lock(
                recovery_lock,
                ttl_secs=_MESSAGE_RECOVERY_LOCK_TTL_SECS,
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
        pending_registry = MessageBusKeys.chat_input_pending_registry()
        for session_id in pending_sessions.keys() | inflight_sessions.keys():
            try:
                raw_routing = pending_sessions.get(session_id)
                if raw_routing is not None:
                    routing = json.loads(raw_routing)
                else:
                    claim = json.loads(inflight_sessions[session_id])
                    routing = claim["payload"]
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
                                await self._bus.registry_del(
                                    pending_registry,
                                    session_id,
                                )
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
            await self._dispatch_one(
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                kind=kind,
                raw_input=payload.get("input"),
            )

    async def _dispatch_one(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        kind: str,
        raw_input: dict | list[dict] | None,
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
                Trigger kind (``wake`` / ``resume`` / ``message`` /
                ``queued_message``); see the module docstring.
            raw_input (`dict | list[dict] | None`):
                Serialised input event for ``resume`` triggers, else
                ``None``.
        """
        is_resume = kind == MessageBusKeys.WAKEUP_KIND_RESUME
        is_message = kind == MessageBusKeys.WAKEUP_KIND_MESSAGE
        is_queued_message = kind == MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE

        # Queued-message triggers are only hints. Avoid storage/lock work
        # for stale completion nudges after the durable queue is empty.
        if is_queued_message:
            pending = await self._bus.queue_read(
                MessageBusKeys.chat_inputs(session_id),
                max_count=1,
            )
            claimed = await self._bus.registry_exists(
                MessageBusKeys.chat_input_inflight_registry(),
                session_id,
            )
            if not pending and not claimed:
                return
            if await self._bus.is_locked(
                MessageBusKeys.chat_input_dispatch_lock(session_id),
            ):
                return
        # ``resume`` and ``message`` both carry input that must be
        # delivered — never dropped while the session is busy.
        carries_input = is_resume or is_message

        # Parse the carried input early so every downstream path
        # (lock-retry, spawn-retry) receives a typed object rather than a
        # raw dict.
        input_msg: (
            UserConfirmResultEvent
            | ExternalExecutionResultEvent
            | UserInterruptEvent
            | Msg
            | None
        ) = None
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
            # The durable pending-session registry plus fallback tick
            # revisits browser queues without per-session hot polling.
            # Every other kind carries work that is not durably indexed
            # there, so it must be retried until the run lock is free.
            if not is_queued_message:
                self._schedule_retry(
                    user_id,
                    session_id,
                    agent_id,
                    kind,
                    input_msg,
                )
            return

        # Orphan guard: the queue is unaware of session lifecycle. A
        # trigger enqueued before the session was deleted (e.g. by a
        # BG-task completion callback or a schedule trigger) will still
        # arrive here. Drop it rather than letting ChatService.run crash
        # on a missing storage record.
        session = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session is None:
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

        if is_queued_message and self._is_session_parked(session):
            # A normal user turn cannot satisfy an ASKING/SUBMITTED tool
            # call. Keep it queued until the resume/interrupt control
            # path has finished the parked reply.
            return

        try:
            run_coro = (
                self._drain_chat_inputs(user_id, session_id, agent_id)
                if is_queued_message
                else self._run_resume_serialized(
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    input_msg=input_msg,
                )
                if is_resume
                else self._chat_service.run(
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    input_msg=input_msg,
                )
            )
            task = self._registry.spawn(
                run_coro,
                session_id=session_id,
                name=f"{kind}-run:{session_id}",
            )
            if is_queued_message:
                # Registry cleanup is registered inside spawn() before this
                # callback. A successful/cancelled pump therefore nudges
                # the next turn only after its local run slot is free.
                task.add_done_callback(
                    lambda completed: self._message_pump_done(
                        completed,
                        user_id,
                        session_id,
                        agent_id,
                    ),
                )
            else:
                task.add_done_callback(
                    lambda _completed: self._schedule_message_nudge(
                        user_id,
                        session_id,
                        agent_id,
                    ),
                )
        except RuntimeError:
            # A local run was registered between the running-check and
            # the spawn. Close the unscheduled coroutine first. Browser
            # queue work remains durably indexed; all other trigger kinds
            # must be re-enqueued because no durable queue owns their input.
            run_coro.close()
            if not is_queued_message:
                self._schedule_retry(
                    user_id,
                    session_id,
                    agent_id,
                    kind,
                    input_msg,
                )

    @staticmethod
    def _is_session_parked(session: object) -> bool:
        """Return whether persisted state awaits HITL/external input.

        Args:
            session (`object`):
                Stored session record whose final assistant message is
                inspected for an asking or submitted tool call.

        Returns:
            `bool`:
                Whether an ordinary queued turn must wait for a control
                event to resume the parked reply.
        """
        state = getattr(session, "state", None)
        context = getattr(state, "context", None)
        if not context:
            return False
        last_msg = context[-1]
        if last_msg.role != "assistant":
            return False
        return any(
            call.state in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
            for call in last_msg.get_content_blocks("tool_call")
        )

    async def _run_resume_serialized(
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
        dispatch_lock = self._bus.acquire_lock(
            MessageBusKeys.chat_input_dispatch_lock(session_id),
            ttl_secs=MessageBusKeys.CHAT_INPUT_DISPATCH_TTL_SECS,
        )
        try:
            async with asyncio.timeout(
                MessageBusKeys.CHAT_INPUT_MUTATION_TIMEOUT_SECS,
            ):
                # The bus API has no acquisition-timeout argument, so enter
                # is bounded separately from the long-running lock body.
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

    async def _drain_chat_inputs(
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
        dispatch_lock = self._bus.acquire_lock(
            MessageBusKeys.chat_input_dispatch_lock(session_id),
            ttl_secs=MessageBusKeys.CHAT_INPUT_DISPATCH_TTL_SECS,
        )
        try:
            async with asyncio.timeout(
                MessageBusKeys.CHAT_INPUT_MUTATION_TIMEOUT_SECS,
            ):
                # The bus API has no acquisition-timeout argument, so enter
                # is bounded separately from the long-running lock body.
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
            if session is None or self._is_session_parked(session):
                return False

            try:
                claim = await self._claim_chat_input(session_id)
            except ChatQueueBusyError:
                return False
            if claim is None:
                return False
            payload, claim_state = claim
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
                    "claim for session %s: %r",
                    session_id,
                    payload,
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
                if claim_state == _MESSAGE_INPUT_PERSISTED:
                    failed_event = CustomEvent(
                        name="chat_input_failed",
                        value={
                            "queue_item_id": queue_item_id,
                            "message_ids": message_ids,
                            "message": (
                                "Processing stopped after the input was "
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
                )
                await self._finish_chat_input_claim(session_id)
                return True
            except asyncio.CancelledError:
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
            claims = await self._bus.registry_getall(
                MessageBusKeys.chat_input_inflight_registry(),
            )
            raw_claim = claims.get(session_id)
            payload: dict | None = None
            state = _MESSAGE_CLAIMED
            if raw_claim is not None:
                try:
                    claim = json.loads(raw_claim)
                    payload = claim["payload"]
                    state = claim["state"]
                    if not isinstance(payload, dict) or state not in (
                        _MESSAGE_CLAIMED,
                        _MESSAGE_INPUT_PERSISTED,
                        _MESSAGE_CANCELLED,
                    ):
                        raise ValueError("Invalid chat-input claim.")
                except (KeyError, TypeError, ValueError):
                    logger.exception(
                        "WakeupDispatcher: deleting malformed in-flight "
                        "claim for session %s.",
                        session_id,
                    )
                    await self._bus.registry_del(
                        MessageBusKeys.chat_input_inflight_registry(),
                        session_id,
                    )
                    payload = None

            pending = await self._bus.queue_read(
                MessageBusKeys.chat_inputs(session_id),
                max_count=1,
            )
            if payload is not None:
                if pending and pending[0][1].get("id") == payload.get("id"):
                    await self._bus.queue_drain(
                        MessageBusKeys.chat_inputs(session_id),
                        max_count=1,
                    )
                return payload, state
            if not pending:
                await self._bus.registry_del(
                    MessageBusKeys.chat_input_pending_registry(),
                    session_id,
                )
                return None

            payload = pending[0][1]
            await self._bus.registry_set(
                MessageBusKeys.chat_input_inflight_registry(),
                session_id,
                json.dumps(
                    {"state": _MESSAGE_CLAIMED, "payload": payload},
                ),
            )
            await self._bus.queue_drain(
                MessageBusKeys.chat_inputs(session_id),
                max_count=1,
            )
            return payload, _MESSAGE_CLAIMED

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
                MessageBusKeys.chat_input_inflight_registry(),
                session_id,
                json.dumps({"state": state, "payload": payload}),
            )

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
            await self._bus.registry_del(
                MessageBusKeys.chat_input_inflight_registry(),
                session_id,
            )
            pending = await self._bus.queue_read(
                MessageBusKeys.chat_inputs(session_id),
                max_count=1,
            )
            if not pending:
                await self._bus.registry_del(
                    MessageBusKeys.chat_input_pending_registry(),
                    session_id,
                )

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

    def _message_pump_done(
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
                return
        if not should_nudge:
            return
        self._schedule_message_nudge(user_id, session_id, agent_id)

    def _schedule_message_nudge(
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

        async def _nudge() -> None:
            try:
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

    def _schedule_retry(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        kind: str,
        input_msg: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | UserInterruptEvent
        | Msg
        | None,
    ) -> None:
        """Re-enqueue an input-carrying (``resume``/``message``) trigger
        after a short backoff.

        Spawns a detached timer that sleeps, then re-enqueues the trigger
        (which re-fires the signal, re-driving the drain). This keeps the
        input alive across the window where the running turn still holds
        the session lock, without a hot re-enqueue loop.

        Args:
            user_id (`str`):
                The owning user id.
            session_id (`str`):
                The session to trigger.
            agent_id (`str`):
                The agent that owns the session.
            kind (`str`):
                The trigger kind to re-enqueue (``resume`` / ``message``).
            input_msg:
                The parsed input to redeliver.
        """

        async def _retry() -> None:
            try:
                await asyncio.sleep(_RESUME_RETRY_BACKOFF_SECS)
                await enqueue_run_trigger(
                    self._bus,
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    kind=kind,  # type: ignore[arg-type]
                    inputs=input_msg,
                )
            except asyncio.CancelledError:
                pass
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: failed to re-enqueue %s trigger "
                    "for session %s.",
                    kind,
                    session_id,
                )

        task = asyncio.create_task(
            _retry(),
            name=f"{kind}-retry:{session_id}",
        )
        self._retry_tasks.add(task)
        task.add_done_callback(self._retry_tasks.discard)
