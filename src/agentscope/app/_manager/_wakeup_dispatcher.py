# -*- coding: utf-8 -*-
"""Single per-process dispatcher for cross-session wake-ups.

One asyncio task per process. Subscribes to the shared wake-up signal
channel and drains the durable wake-up queue on each signal. For each
queued entry whose session is idle, spawns a background
:meth:`ChatService.run` call.

All bus keys live on the :class:`MessageBus` base class (see
``enqueue_wakeup``, ``dequeue_wakeups``, ``subscribe_wakeup_signal``,
``session_is_running``), so this file has no hard-coded key strings.
"""
import asyncio
from typing import TYPE_CHECKING, Self

from ..._logging import logger

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from .._service import ChatService


class WakeupDispatcher:
    """One asyncio task per process, draining the shared wake-up queue.

    Args:
        message_bus (`MessageBus`):
            Application message bus. Used for signal subscription,
            queue drain, and ``session_is_running`` checks.
        chat_service (`ChatService`):
            Drives the actual chat run when waking an idle session.
    """

    def __init__(
        self,
        message_bus: "MessageBus",
        chat_service: "ChatService",
    ) -> None:
        """Bind dependencies.

        Args:
            message_bus (`MessageBus`):
                Application message bus.
            chat_service (`ChatService`):
                Drives idle-session wake-ups via :meth:`ChatService.run`.
        """
        self._bus = message_bus
        self._chat_service = chat_service
        self._task: asyncio.Task | None = None
        self._spawned: set[asyncio.Task] = set()

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

            task = asyncio.create_task(
                self._chat_service.run(
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    input_msg=None,
                ),
                name=f"wakeup-run:{session_id}",
            )
            self._spawned.add(task)
            task.add_done_callback(self._spawned.discard)
