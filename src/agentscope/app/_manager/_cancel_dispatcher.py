# -*- coding: utf-8 -*-
"""Single per-process dispatcher for cross-process cancels.

Subscribes to two bus channels:

1. **Session cancel** — cancel all local work for a session (chat run
   + all BG tasks). Triggered by session deletion or explicit abort.
2. **Task cancel** — cancel a single BG task by task_id. Triggered by
   the :class:`ToolStop` agent tool when the target task lives on a
   different worker.

Processes whose registry / BG-manager do not hold the targeted session
or task simply do no work — the publisher does not need to know which
worker holds what; it broadcasts and lets each holder self-select.
"""
import asyncio
from typing import TYPE_CHECKING, Self

from ..._logging import logger
from ..message_bus import MessageBusKeys

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ._background_task_manager import BackgroundTaskManager
    from ._chat_run_registry import ChatRunRegistry


class CancelDispatcher:
    """Subscribes to bus cancel channels and cancels matching local
    tasks.

    Args:
        message_bus (`MessageBus`):
            Application message bus.
        registry (`ChatRunRegistry`):
            The per-process chat-run registry whose tasks may be
            cancelled.
        bg_manager (`BackgroundTaskManager`):
            The per-process background task manager.
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
        self._session_task: asyncio.Task | None = None
        self._task_cancel_task: asyncio.Task | None = None
        self._interrupt_task: asyncio.Task | None = None

    async def __aenter__(self) -> Self:
        """Start both dispatcher loops and wait until their bus
        subscriptions are live.

        Returns:
            `Self`: This dispatcher instance.
        """
        session_ready = asyncio.Event()
        task_ready = asyncio.Event()
        interrupt_ready = asyncio.Event()

        self._session_task = asyncio.create_task(
            self._session_cancel_loop(session_ready),
            name="cancel-dispatcher:session",
        )
        self._task_cancel_task = asyncio.create_task(
            self._task_cancel_loop(task_ready),
            name="cancel-dispatcher:task",
        )
        self._interrupt_task = asyncio.create_task(
            self._interrupt_loop(interrupt_ready),
            name="cancel-dispatcher:interrupt",
        )

        await session_ready.wait()
        await task_ready.wait()
        await interrupt_ready.wait()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Cancel all dispatcher loops on context exit."""
        for task in (
            self._session_task,
            self._task_cancel_task,
            self._interrupt_task,
        ):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._session_task = None
        self._task_cancel_task = None
        self._interrupt_task = None

    # ------------------------------------------------------------------
    # Session-level cancel loop
    # ------------------------------------------------------------------

    async def _session_cancel_loop(self, ready: asyncio.Event) -> None:
        """Subscribe to session cancel channel and act on each signal.

        Args:
            ready (`asyncio.Event`):
                Signalled after the underlying SUBSCRIBE completes.
        """
        try:
            async for payload in self._bus.subscribe(
                MessageBusKeys.session_cancel_channel(),
                on_ready=ready.set,
            ):
                sid = payload.get("session_id")
                if isinstance(sid, str):
                    self._cancel_session(sid)
        except Exception:
            logger.exception(
                "CancelDispatcher session-cancel loop crashed.",
            )
        finally:
            # Unblock ``__aenter__`` even if subscribe failed before
            # ``on_ready`` ran, so startup cannot deadlock.
            ready.set()

    def _cancel_session(self, session_id: str) -> None:
        """Cancel every locally-tracked task for a session.

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

    # ------------------------------------------------------------------
    # Task-level cancel loop
    # ------------------------------------------------------------------

    async def _task_cancel_loop(self, ready: asyncio.Event) -> None:
        """Subscribe to the task cancel channel and act on each signal.

        Args:
            ready (`asyncio.Event`):
                Signalled after the underlying SUBSCRIBE completes.
        """
        try:
            async for payload in self._bus.subscribe(
                MessageBusKeys.task_cancel_channel(),
                on_ready=ready.set,
            ):
                task_id = payload.get("task_id")
                if not isinstance(task_id, str):
                    continue
                cancelled = self._bg_manager.cancel_task(task_id)
                if cancelled:
                    logger.info(
                        "CancelDispatcher: cancelled local BG task %s "
                        "via task-level broadcast.",
                        task_id,
                    )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "CancelDispatcher task-cancel loop crashed.",
            )
        finally:
            # Unblock ``__aenter__`` even if subscribe failed before
            # ``on_ready`` ran, so startup cannot deadlock.
            ready.set()

    # ------------------------------------------------------------------
    # Session-level interrupt loop
    # ------------------------------------------------------------------

    async def _interrupt_loop(self, ready: asyncio.Event) -> None:
        """Subscribe to the session interrupt channel and cancel matching
        local tasks.

        Args:
            ready (`asyncio.Event`):
                Signalled after the underlying SUBSCRIBE completes.
        """
        try:
            async for payload in self._bus.subscribe(
                MessageBusKeys.session_interrupt_channel(),
                on_ready=ready.set,
            ):
                sid = payload.get("session_id")
                if isinstance(sid, str):
                    self._interrupt_session(sid)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "CancelDispatcher interrupt loop crashed.",
            )
        finally:
            ready.set()

    def _interrupt_session(self, session_id: str) -> None:
        """Cancel the local chat-run task for a session.

        Unlike ``_cancel_session``, this method only cancels the
        chat-run asyncio task—it does **not** cancel background tasks.
        The agent handles ``CancelledError`` gracefully at each
        breakpoint, preserving partial context.

        Args:
            session_id (`str`):
                The session whose chat run should be interrupted.
        """
        task = self._registry.get(session_id)
        if task is not None and not task.done():
            logger.info(
                "CancelDispatcher: interrupting local chat run for "
                "session %s",
                session_id,
            )
            task.cancel()
