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
                run as a background task.
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
            # Only remove the entry if it still points at this task —
            # a fresh spawn for the same sid may have replaced it.
            if self._tasks.get(session_id) is t:
                self._tasks.pop(session_id, None)

        task.add_done_callback(_cleanup)
        return task

    def get(self, session_id: str) -> asyncio.Task | None:
        """Return the registered task for ``session_id``, or ``None``.

        Args:
            session_id (`str`):
                The session whose task to look up.

        Returns:
            `asyncio.Task | None`:
                The task if one is currently registered for the
                session, else ``None``.
        """
        return self._tasks.get(session_id)

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
            task.cancel()
        # Wait for every cancel to land; swallow CancelledError per task.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
