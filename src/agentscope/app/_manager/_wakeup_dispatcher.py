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
from typing import TYPE_CHECKING, Self

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
from ._chat_input_queue import ChatInputQueue

from .._bus_ops import (
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

# Delay before re-queuing a trigger whose target session still holds
# its run lock. Short enough to feel instant to the user, long enough
# to avoid a hot re-enqueue loop while the lock is held.
_RESUME_RETRY_BACKOFF_SECS = 0.1

# Durably indexed pending queues are revisited at this low frequency.
# This is a crash/lost-signal safety net, not a per-session hot poll.
_MESSAGE_FALLBACK_TICK_SECS = 30.0


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
        self._chat_queue = ChatInputQueue(message_bus, storage, chat_service)
        # Detached resume timers. Held
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
        self._chat_queue.schedule_recovery()
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
        await self._chat_queue.close()

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
        while True:
            await asyncio.sleep(_MESSAGE_FALLBACK_TICK_SECS)
            try:
                # Never drain here: Redis queue_drain is XRANGE + XDEL and
                # therefore requires a single in-process consumer. All
                # actual draining remains serialized in ``_loop``.
                await self._bus.publish(MessageBusKeys.wakeup_signal(), {})
            except Exception:  # pylint: disable=broad-except
                logger.exception("WakeupDispatcher fallback signal failed.")
            try:
                self._chat_queue.schedule_recovery()
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher recovery scheduling failed.",
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

        try:
            run_coro = (
                self._chat_queue.drain_chat_inputs(
                    user_id,
                    session_id,
                    agent_id,
                )
                if is_queued_message
                else self._chat_queue.run_resume_serialized(
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
                    lambda completed: self._chat_queue.message_pump_done(
                        completed,
                        user_id,
                        session_id,
                        agent_id,
                    ),
                )
            else:
                task.add_done_callback(
                    lambda _completed: self._chat_queue.schedule_message_nudge(
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
