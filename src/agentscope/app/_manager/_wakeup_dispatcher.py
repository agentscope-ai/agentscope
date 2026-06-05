# -*- coding: utf-8 -*-
"""Single per-process dispatcher for cross-session wake-ups.

One asyncio task per process. Subscribes to the shared wake-up signal
channel and drains the durable wake-up queue on each signal. For each
queued entry whose session is idle, spawns a background
:meth:`ChatService.run` call through the shared
:class:`ChatRunRegistry`, so the spawned task can be looked up and
cancelled by :class:`CancelDispatcher`.

All bus keys live on the :class:`MessageBus` base class (see
``enqueue_wakeup``, ``dequeue_wakeups``, ``subscribe_wakeup_signal``,
``session_is_running``), so this file has no hard-coded key strings.
"""
import asyncio
from typing import TYPE_CHECKING, Self

from ..._logging import logger

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import StorageBase
    from .._service import ChatService
    from ._chat_run_registry import ChatRunRegistry


class WakeupDispatcher:
    """One asyncio task per process, draining the shared wake-up queue.

    Args:
        message_bus (`MessageBus`):
            Application message bus. Used for signal subscription,
            queue drain, and ``session_is_running`` checks.
        storage (`StorageBase`):
            Persistent storage backend. Consulted before spawning a
            run so wake-ups whose target session has been deleted are
            dropped instead of crashing :class:`ChatService.run`.
        chat_service (`ChatService`):
            Drives the actual chat run when waking an idle session.
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
                Drives idle-session wake-ups via :meth:`ChatService.run`.
            chat_run_registry (`ChatRunRegistry`):
                Shared chat-run registry to spawn into.
        """
        self._bus = message_bus
        self._storage = storage
        self._chat_service = chat_service
        self._registry = chat_run_registry
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> Self:
        """Start the dispatcher loop and wait until its bus
        subscription is live.

        Also performs an initial drain right after subscription so
        wake-ups produced while this process was down (durable in
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
        """Cancel the dispatcher loop on context exit."""
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
                wake-up immediately after start without racing.
        """
        try:
            async for _signal in self._bus.subscribe_wakeup_signal(
                on_ready=ready.set,
            ):
                await self._drain_and_dispatch()
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "WakeupDispatcher loop crashed; subscription ended.",
            )

    async def _drain_and_dispatch(self) -> None:
        """Read up to a batch of wake-up entries and dispatch them."""
        try:
            entries = await self._bus.dequeue_wakeups(max_count=64)
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
                    "WakeupDispatcher: skipping malformed wake-up entry %r",
                    payload,
                )
                continue

            if await self._bus.session_is_running(session_id):
                continue

            # Orphan guard: the wake-up queue is unaware of session
            # lifecycle. A wake-up enqueued before the session was
            # deleted (e.g. by a BG task completion callback or a
            # schedule trigger) will still arrive here. Drop it
            # rather than letting ChatService.run crash on a missing
            # storage record.
            if (
                await self._storage.get_session(
                    user_id,
                    agent_id,
                    session_id,
                )
                is None
            ):
                logger.warning(
                    "WakeupDispatcher: dropping wake-up for session %s "
                    "(agent %s, user %s) — session no longer exists in "
                    "storage; the wake-up was likely enqueued before "
                    "the session was deleted.",
                    session_id,
                    agent_id,
                    user_id,
                )
                continue

            try:
                self._registry.spawn(
                    self._chat_service.run(
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                        input_msg=None,
                    ),
                    session_id=session_id,
                    name=f"wakeup-run:{session_id}",
                )
            except RuntimeError:
                # Another spawn won the race for this session in this
                # process; the existing run will drain the inbox.
                logger.debug(
                    "WakeupDispatcher: skipping wake-up for session %s; "
                    "a local run is already registered.",
                    session_id,
                )
