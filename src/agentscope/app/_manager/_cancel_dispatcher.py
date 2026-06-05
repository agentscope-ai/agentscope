# -*- coding: utf-8 -*-
"""Single per-process dispatcher for cross-process session cancels.

Subscribes to the bus's shared cancel-broadcast channel. For each
incoming ``session_id``, performs every session-scoped cancel a worker
process can do locally:

1. Look the session up in the local :class:`ChatRunRegistry` — if a
   chat-run task is tracked here, cancel it.
2. Ask the local :class:`BackgroundTaskManager` to cancel every BG
   task it tracks for that session.

Processes whose registry / BG-manager do not hold anything for the
session simply do no work — the publisher does not need to know which
worker holds which piece; it broadcasts and lets each holder self-select.

Symmetric to :class:`WakeupDispatcher`: both are one asyncio task per
process; one starts runs in response to wake-ups, the other ends them
in response to cancels.
"""
import asyncio
from typing import TYPE_CHECKING, Self

from ..._logging import logger

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ._background_task_manager import BackgroundTaskManager
    from ._chat_run_registry import ChatRunRegistry


class CancelDispatcher:
    """Subscribes to the bus cancel channel and cancels every local
    session-scoped task (chat run + BG tasks) on match.

    Args:
        message_bus (`MessageBus`):
            Application message bus. Used for
            :meth:`~agentscope.app.message_bus.MessageBus.
            session_subscribe_cancel`.
        registry (`ChatRunRegistry`):
            The per-process chat-run registry whose tasks may be
            cancelled.
        bg_manager (`BackgroundTaskManager`):
            The per-process background task manager. Its
            :meth:`~BackgroundTaskManager.cancel_session_tasks` is
            invoked on every incoming cancel; it returns silently when
            no local BG task matches the session.
    """

    def __init__(
        self,
        message_bus: "MessageBus",
        registry: "ChatRunRegistry",
        bg_manager: "BackgroundTaskManager",
    ) -> None:
        """Bind dependencies.

        Args:
            message_bus (`MessageBus`):
                Application message bus.
            registry (`ChatRunRegistry`):
                The per-process chat-run registry.
            bg_manager (`BackgroundTaskManager`):
                The per-process background task manager.
        """
        self._bus = message_bus
        self._registry = registry
        self._bg_manager = bg_manager
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> Self:
        """Start the dispatcher loop and wait until its bus
        subscription is live.

        Blocking on the readiness signal means a process that publishes
        a cancel immediately after the dispatcher starts will not lose
        the message to a SUBSCRIBE/PUBLISH race.

        Returns:
            `Self`: This dispatcher instance.
        """
        ready = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(ready),
            name="cancel-dispatcher",
        )
        await ready.wait()
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
        """Long-lived loop: yield each incoming cancel and act on it.

        Args:
            ready (`asyncio.Event`):
                Signalled after the underlying SUBSCRIBE completes.
                :meth:`__aenter__` blocks on this so callers can
                publish a cancel immediately after start without
                racing the subscription.
        """
        try:
            async for session_id in self._bus.session_subscribe_cancel(
                on_ready=ready.set,
            ):
                self._cancel_local(session_id)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "CancelDispatcher loop crashed; subscription ended.",
            )

    def _cancel_local(self, session_id: str) -> None:
        """Cancel every locally-tracked task for ``session_id``.

        Fans out to:

        - the chat-run task in :class:`ChatRunRegistry`, if registered
          on this process;
        - every BG task in :class:`BackgroundTaskManager` whose owner
          session matches.

        Both lookups are silent no-ops when the local process holds no
        matching state.

        Args:
            session_id (`str`):
                The session whose runs and BG tasks should be cancelled.
        """
        task = self._registry.get(session_id)
        if task is not None and not task.done():
            logger.info(
                "CancelDispatcher: cancelling local chat run for "
                "session %s",
                session_id,
            )
            task.cancel()

        bg_cancelled = self._bg_manager.cancel_session_tasks(session_id)
        if bg_cancelled:
            logger.info(
                "CancelDispatcher: cancelled %d local BG task(s) for "
                "session %s",
                bg_cancelled,
                session_id,
            )
