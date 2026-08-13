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

No kind is ever dropped. A trigger whose session still holds its run
lock is re-queued after a short backoff until the lock frees, then
spawned.

All bus keys live on the :class:`MessageBus` base class (see
``enqueue_wakeup`` / ``enqueue_input``, ``dequeue_wakeups``,
``subscribe_wakeup_signal``, ``session_is_running``), so this file has
no hard-coded key strings.
"""
import asyncio
import os
import socket
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
from .._bus_ops import publish_session_event

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

# Delay before nudging dispatchers to retry a released trigger. Short
# enough to feel instant, long enough to avoid a hot re-drain loop while
# a session lock is held.
_REDRAIN_BACKOFF_SECS = 0.1


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
        self._consumer = f"{socket.gethostname()}:{os.getpid()}"
        # Detached backoff timers for re-drain nudges. Held so they are
        # not garbage-collected mid-sleep and cancelled on shutdown.
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
        """Claim a batch of trigger entries and dispatch each.

        An entry is acknowledged once its run has been spawned, and
        released when it could not be — so a trigger outlives the
        process that picked it up.
        """
        key = MessageBusKeys.wakeup_queue()
        try:
            entries = await self._bus.queue_claim(
                key,
                consumer=self._consumer,
                max_count=64,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception("WakeupDispatcher: claiming triggers failed.")
            return

        deferred: list[str] = []
        for entry_id, payload in entries:
            handled = True
            try:
                handled = await self._dispatch_one(
                    user_id=payload["user_id"],
                    session_id=payload["session_id"],
                    agent_id=payload["agent_id"],
                    # Entries from older producers omit ``kind``.
                    kind=payload.get(
                        "kind",
                        MessageBusKeys.WAKEUP_KIND_WAKE,
                    ),
                    raw_input=payload.get("input"),
                )
            except (KeyError, TypeError):
                logger.warning(
                    "WakeupDispatcher: dropping malformed trigger entry %r",
                    payload,
                )
            if handled:
                await self._bus.queue_ack(
                    key,
                    consumer=self._consumer,
                    entry_ids=[entry_id],
                )
            else:
                deferred.append(entry_id)

        if deferred:
            await self._bus.queue_release(
                key,
                consumer=self._consumer,
                entry_ids=deferred,
            )
            self._schedule_redrain()

    async def _dispatch_one(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        kind: str,
        raw_input: dict | None,
    ) -> bool:
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

        Returns:
            `bool`:
                ``True`` when the trigger is done with, ``False`` when
                it must be handed back for a later attempt.
        """
        is_resume = kind == MessageBusKeys.WAKEUP_KIND_RESUME
        is_message = kind == MessageBusKeys.WAKEUP_KIND_MESSAGE
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
                return True
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
                return True

        if await self._bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        ):
            # The session is busy, so nothing can be spawned right now:
            # re-queue after a short backoff rather than dropping. A run
            # that has already taken its last look at the inbox still
            # holds the lock while it persists, so a held lock is no
            # evidence that anyone will consume what was just pushed.
            return False

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
            return True

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
            return True
        except RuntimeError:
            # A local run was registered between the running-check and
            # the spawn; hand the trigger back for a later attempt.
            return False

    def _schedule_redrain(self) -> None:
        """Nudge dispatchers to drain again after a short backoff.

        Released entries stay in the queue, so this only has to make
        somebody look again — nothing is carried in memory, and a
        dispatcher dying mid-backoff costs a retry, not a trigger.
        """

        async def _redrain() -> None:
            try:
                await asyncio.sleep(_REDRAIN_BACKOFF_SECS)
                await self._bus.publish(MessageBusKeys.wakeup_signal(), {})
            except asyncio.CancelledError:
                pass
            except Exception:  # pylint: disable=broad-except
                logger.exception("WakeupDispatcher: re-drain nudge failed.")

        task = asyncio.create_task(_redrain(), name="wakeup-redrain")
        self._retry_tasks.add(task)
        task.add_done_callback(self._retry_tasks.discard)
