# -*- coding: utf-8 -*-
"""Single per-process dispatcher for cross-process cancels.

Subscribes to two bus channels:

1. **Session cancel** — cancel all local work for a session (chat run,
   realtime run, and all BG tasks). Triggered by session deletion or
   explicit abort.
2. **Task cancel** — cancel a single BG task by task_id. Triggered by
   the :class:`ToolStop` agent tool when the target task lives on a
   different worker.

Processes whose registry / BG-manager do not hold the targeted session
or task simply do no work — the publisher does not need to know which
worker holds what; it broadcasts and lets each holder self-select.

Each subscription is retried with bounded backoff if the bus drops the
pub/sub, so one disconnect does not stop cancel delivery on this
process.
"""
import asyncio
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Self

from ..._logging import logger
from ..message_bus import MessageBusKeys

# Same curve as the channel and schedule listeners.
_SUBSCRIBE_BACKOFF_INITIAL_SECS = 1.0
_SUBSCRIBE_BACKOFF_MAX_SECS = 30.0

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from .._service._webrtc_session import WebRTCSession
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
        realtime_connections (`Mapping`, optional):
            The per-process browser realtime sessions. The shared cancel
            broadcast closes matching runners on whichever process owns
            them.
    """

    def __init__(
        self,
        message_bus: "MessageBus",
        registry: "ChatRunRegistry",
        bg_manager: "BackgroundTaskManager",
        realtime_connections: Mapping[
            tuple[str, str],
            "WebRTCSession",
        ]
        | None = None,
    ) -> None:
        """Bind dependencies.

        Args:
            message_bus (`MessageBus`):
                Application message bus.
            registry (`ChatRunRegistry`):
                The per-process chat-run registry.
            bg_manager (`BackgroundTaskManager`):
                The per-process background task manager.
            realtime_connections (`Mapping`, optional):
                The per-process browser realtime sessions.
        """
        self._bus = message_bus
        self._registry = registry
        self._bg_manager = bg_manager
        self._realtime_connections = (
            realtime_connections if realtime_connections is not None else {}
        )
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

        def _on_payload(payload: dict) -> None:
            sid = payload.get("session_id")
            if isinstance(sid, str):
                self._cancel_session(sid)

        await self._consume_subscription(
            MessageBusKeys.session_cancel_channel(),
            ready,
            _on_payload,
            name="session-cancel",
        )

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

        realtime_cancelled = 0
        for (_, candidate_session_id), runner in list(
            self._realtime_connections.items(),
        ):
            if candidate_session_id == session_id:
                runner.request_close()
                realtime_cancelled += 1
        if realtime_cancelled:
            logger.info(
                "CancelDispatcher: closing %d local realtime run(s) for "
                "session %s",
                realtime_cancelled,
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

        def _on_payload(payload: dict) -> None:
            task_id = payload.get("task_id")
            if not isinstance(task_id, str):
                return
            cancelled = self._bg_manager.cancel_task(task_id)
            if cancelled:
                logger.info(
                    "CancelDispatcher: cancelled local BG task %s "
                    "via task-level broadcast.",
                    task_id,
                )

        await self._consume_subscription(
            MessageBusKeys.task_cancel_channel(),
            ready,
            _on_payload,
            name="task-cancel",
        )

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

        def _on_payload(payload: dict) -> None:
            sid = payload.get("session_id")
            if isinstance(sid, str):
                self._interrupt_session(sid)

        await self._consume_subscription(
            MessageBusKeys.session_interrupt_channel(),
            ready,
            _on_payload,
            name="interrupt",
        )

    async def _consume_subscription(
        self,
        channel: str,
        ready: asyncio.Event,
        on_payload: Callable[[dict], None],
        *,
        name: str,
    ) -> None:
        """Subscribe until cancelled, reconnecting when the pub/sub drops.

        A generator that returns or raises is logged and retried with
        bounded exponential backoff. ``asyncio.CancelledError`` exits
        without sleeping so :meth:`__aexit__` stays prompt. ``ready`` is
        set from ``on_ready`` and again in ``finally``, including when
        the first subscribe fails before ``on_ready``.

        Args:
            channel (`str`):
                Bus channel name.
            ready (`asyncio.Event`):
                Signalled after the underlying SUBSCRIBE completes.
            on_payload (`Callable[[dict], None]`):
                Invoked for each delivered payload.
            name (`str`):
                Short loop name used in log lines.
        """
        backoff = _SUBSCRIBE_BACKOFF_INITIAL_SECS
        while True:
            try:
                async for payload in self._bus.subscribe(
                    channel,
                    on_ready=ready.set,
                ):
                    # A delivered payload means the subscription is
                    # healthy, so the next drop starts again at the
                    # initial delay. Same rule as the schedule listener.
                    backoff = _SUBSCRIBE_BACKOFF_INITIAL_SECS
                    on_payload(payload)
            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                raise
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "CancelDispatcher %s loop crashed.",
                    name,
                )
            else:
                logger.warning(
                    "CancelDispatcher %s subscription ended.",
                    name,
                )
            finally:
                # Unblock ``__aenter__`` even if subscribe failed before
                # ``on_ready`` ran, so startup cannot deadlock.
                ready.set()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _SUBSCRIBE_BACKOFF_MAX_SECS)

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
