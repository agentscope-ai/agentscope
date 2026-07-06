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

- ``wake`` (idle-session wake-up, ``input_msg=None``): skipped while the
  session is already running — the live run will drain the inbox.
- ``resume`` (a parked HITL run being fed its result): must *not* be
  skipped while running, because the session is typically still running
  the parked tail at trigger time. It is re-queued after a short backoff
  until the parked run releases its session lock, then spawned with the
  carried input event.

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
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message_bus import MessageBusKeys
from .._bus_ops import enqueue_run_trigger

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

# Delay before re-queuing a ``resume`` trigger whose target session is
# still running (the parked run is finishing and about to free its
# lock). Short enough to feel instant to the user, long enough to avoid
# a hot re-enqueue loop while the lock is held.
_RESUME_RETRY_BACKOFF_SECS = 0.1


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
        # Detached backoff timers for deferred ``resume`` re-enqueues.
        # Held so they are not garbage-collected mid-sleep and can be
        # cancelled on shutdown.
        self._retry_tasks: set[asyncio.Task] = set()

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
        retries = list(self._retry_tasks)
        for retry in retries:
            retry.cancel()
        for retry in retries:
            try:
                await retry
            except asyncio.CancelledError:
                pass
        self._retry_tasks.clear()
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
            entries = [payload for _entry_id, payload in raw_entries]
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
        raw_input: dict | None,
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
        """
        is_resume = kind == MessageBusKeys.WAKEUP_KIND_RESUME

        # Parse the resume input early so every downstream path
        # (lock-retry, spawn-retry) receives a typed event object
        # rather than a raw dict.
        input_msg: UserConfirmResultEvent | ExternalExecutionResultEvent | None
        input_msg = None
        if is_resume:
            if raw_input is None:
                logger.warning(
                    "WakeupDispatcher: dropping resume trigger for session "
                    "%s — no input event carried.",
                    session_id,
                )
                return
            try:
                input_msg = _RESUME_INPUT_ADAPTER.validate_python(raw_input)
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: dropping resume trigger for session "
                    "%s — input event failed to parse: %r",
                    session_id,
                    raw_input,
                )
                return

        if await self._bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        ):
            if is_resume:
                # The session is busy finishing its parked tail. Do NOT
                # drop the resume — re-queue it after a short backoff so
                # it lands once the parked run releases its lock.
                self._schedule_resume_retry(
                    user_id,
                    session_id,
                    agent_id,
                    input_msg,
                )
            # ``wake`` triggers are safe to drop while running — the
            # live run drains the inbox itself.
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
            # the spawn. For ``wake`` that run will drain the inbox; for
            # ``resume`` re-queue so the result is not lost.
            if is_resume:
                self._schedule_resume_retry(
                    user_id,
                    session_id,
                    agent_id,
                    input_msg,
                )
            else:
                logger.debug(
                    "WakeupDispatcher: skipping wake trigger for session "
                    "%s; a local run is already registered.",
                    session_id,
                )

    def _schedule_resume_retry(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None,
    ) -> None:
        """Re-enqueue a ``resume`` trigger after a short backoff.

        Spawns a detached timer that sleeps, then re-enqueues the resume
        (which re-fires the signal, re-driving the drain). This keeps the
        resume alive across the window where the parked run still holds
        the session lock, without a hot re-enqueue loop.

        Args:
            user_id (`str`):
                The owning user id.
            session_id (`str`):
                The session to resume.
            agent_id (`str`):
                The agent that owns the session.
            input_msg:
                The parsed input event to redeliver.
        """

        async def _retry() -> None:
            try:
                await asyncio.sleep(_RESUME_RETRY_BACKOFF_SECS)
                await enqueue_run_trigger(
                    self._bus,
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    kind=MessageBusKeys.WAKEUP_KIND_RESUME,
                    inputs=input_msg,
                )
            except asyncio.CancelledError:
                pass
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "WakeupDispatcher: failed to re-enqueue resume trigger "
                    "for session %s.",
                    session_id,
                )

        task = asyncio.create_task(
            _retry(),
            name=f"resume-retry:{session_id}",
        )
        self._retry_tasks.add(task)
        task.add_done_callback(self._retry_tasks.discard)
