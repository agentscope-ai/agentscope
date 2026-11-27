# -*- coding: utf-8 -*-
"""Per-process registry of in-flight ``ChatService.run`` asyncio tasks.

Owns the asyncio.Task handles only — it is not the public cancel
entry point. The cross-process cancel path goes through the bus's
:meth:`~agentscope.app.message_bus.MessageBus.session_publish_cancel`
broadcast, picked up locally by
:class:`~agentscope.app._manager.CancelDispatcher`, which then looks
up the task here and calls ``.cancel()`` on it.

A given ``session_id`` can have at most one entry. Concurrent runs for
the same session are already prevented at a cluster level by
:meth:`~agentscope.app.message_bus.MessageBus.session_run` (the
distributed lock), so a second :meth:`spawn` for the same id is treated
as a programming error.
"""
import asyncio
from typing import Coroutine, Self

from ..._logging import logger


CHAT_RUN_SHUTDOWN = "agentscope:shutdown"


class ChatRunRegistry:
    """In-process index of active chat-run asyncio tasks, keyed by
    session id.

    Used by :class:`~agentscope.app._manager.CancelDispatcher` to find
    and cancel the local task for a given session, and by the lifespan
    to cancel any leftover runs on application shutdown.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._tasks: dict[str, asyncio.Task] = {}
        self._reply_ids: dict[asyncio.Task, str] = {}
        self._queue_ids: dict[asyncio.Task, str] = {}

    def spawn(
        self,
        coro: Coroutine,
        *,
        session_id: str,
        name: str | None = None,
    ) -> asyncio.Task:
        """Create and register an asyncio task that runs ``coro``.

        The task auto-removes from the registry when it finishes (via
        ``add_done_callback``).

        Args:
            coro (`Coroutine`):
                A coroutine — typically ``chat_service.run(...)`` — to
                run as a background task. Closed if the spawn is rejected.
            session_id (`str`):
                The session this run belongs to. Used as the registry
                key for later cancel lookup.
            name (`str | None`, optional):
                Optional task name passed through to
                :func:`asyncio.create_task`. Defaults to
                ``f"chat-run:{session_id}"``.

        Returns:
            `asyncio.Task`:
                The created task. Callers normally do not need to keep
                the reference — the registry holds it for the task's
                lifetime.

        Raises:
            `RuntimeError`:
                When a non-finished task is already registered for
                ``session_id``. Callers are expected to coordinate via
                the distributed session lock before spawning.
        """
        existing = self._tasks.get(session_id)
        if existing is not None and not existing.done():
            coro.close()
            raise RuntimeError(
                f"Session {session_id!r} already has an active chat run "
                "in this process.",
            )

        task = asyncio.create_task(
            coro,
            name=name or f"chat-run:{session_id}",
        )
        self._tasks[session_id] = task

        def _cleanup(t: asyncio.Task) -> None:
            self._reply_ids.pop(t, None)
            self._queue_ids.pop(t, None)
            # Only remove the entry if it still points at this task —
            # a fresh spawn for the same sid may have replaced it.
            if self._tasks.get(session_id) is t:
                self._tasks.pop(session_id, None)

        task.add_done_callback(_cleanup)
        return task

    def set_reply(self, session_id: str, reply_id: str | None) -> None:
        """Bind or clear the reply owned by the current registered task.

        Args:
            session_id (`str`):
                Session whose registered task is executing this call.
            reply_id (`str | None`):
                Active reply, or ``None`` after its terminal event.
        """
        task = asyncio.current_task()
        if task is None or self._tasks.get(session_id) is not task:
            return
        if reply_id is None:
            self._reply_ids.pop(task, None)
        else:
            # Queue targeting ends permanently at the first reply start.
            self._queue_ids.pop(task, None)
            self._reply_ids[task] = reply_id

    def set_queue_input(
        self,
        session_id: str,
        queue_item_id: str | None,
    ) -> None:
        """Bind the current task only while its queued input initializes."""
        task = asyncio.current_task()
        if task is None or self._tasks.get(session_id) is not task:
            return
        if queue_item_id is None:
            self._queue_ids.pop(task, None)
        else:
            self._queue_ids[task] = queue_item_id

    def get(
        self,
        session_id: str,
        *,
        reply_id: str | None = None,
        queue_item_id: str | None = None,
    ) -> asyncio.Task | None:
        """Return the registered task for ``session_id``, or ``None``.

        Args:
            session_id (`str`):
                The session whose task to look up.
            reply_id (`str | None`, optional):
                Require the task to own this active reply when provided.
            queue_item_id (`str | None`, optional):
                Require this input to still be initializing, before reply.

        Returns:
            `asyncio.Task | None`:
                The task if one is currently registered for the
                session, else ``None``.
        """
        task = self._tasks.get(session_id)
        if task is None:
            return None
        if reply_id is not None and self._reply_ids.get(task) != reply_id:
            return None
        if (
            queue_item_id is not None
            and self._queue_ids.get(task) != queue_item_id
        ):
            return None
        return task

    async def __aenter__(self) -> Self:
        """No-op enter; the registry has no startup work.

        Returns:
            `Self`: This registry instance.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Cancel every still-running task on application shutdown.

        Each task is cancelled and awaited so its ``finally`` blocks
        and any ``async with`` cleanups (notably the bus's session
        run-lock release) execute before the process exits.
        """
        if not self._tasks:
            return
        logger.info(
            "ChatRunRegistry shutdown: cancelling %d in-flight chat run(s).",
            len(self._tasks),
        )
        tasks = list(self._tasks.values())
        for task in tasks:
            # Preserve an explicit Stop that is already being delivered.
            if not task.cancelling():
                task.cancel(CHAT_RUN_SHUTDOWN)
        # Wait for every cancel to land; swallow CancelledError per task.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._reply_ids.clear()
        self._queue_ids.clear()
