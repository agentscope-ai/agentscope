# -*- coding: utf-8 -*-
"""Pre-warming for workspace managers.

Provisioning a sandboxed workspace — image pull, container/sandbox
create, gateway bootstrap, health poll — costs seconds to tens of
seconds, and today every one of those seconds lands on the session that
asked for it. :class:`WorkspacePrewarmMixin` keeps a small buffer of
workspaces built *ahead of demand* so a session can be handed one that
is already running.

A buffered slot is an :class:`asyncio.Future`, not a finished
workspace. A taker awaits its slot: ready slots return instantly, and a
slot still being built is simply awaited to completion. So a request
that arrives mid-build waits out the remainder of a build already in
flight rather than starting a second one of its own.

Buffered workspaces are never recycled. One is built, handed out once,
and then lives under the manager's ordinary cache and TTL rules — there
is no check-in path, hence no reset step and no cross-user state to
scrub.
"""

import asyncio
from collections import deque

from ..._logging import logger
from ...workspace import WorkspaceBase


class WorkspacePrewarmMixin:
    """A buffer of pre-built, unassigned workspaces.

    Mix in *before* :class:`WorkspaceManagerBase` and implement
    :meth:`_create_prewarmed` and :meth:`_adopt_prewarmed`. Drive the
    buffer from the manager's ``__aenter__`` / ``__aexit__`` via
    :meth:`_start_prewarm` and :meth:`_stop_prewarm`.
    """

    def __init__(self, *, prewarm: int = 0, max_creating: int = 4) -> None:
        """Size the buffer and the build concurrency.

        Args:
            prewarm (`int`, defaults to `0`):
                How many unassigned workspaces to keep ready. ``0``
                disables pre-warming entirely.
            max_creating (`int`, defaults to `4`):
                Ceiling on builds running at once. A burst of requests
                queues behind this instead of stampeding the Docker
                daemon or the provider's API.
        """
        self._prewarm = prewarm
        self._slots: deque[asyncio.Future] = deque()
        self._prewarm_tasks: set[asyncio.Task] = set()
        self._creating = asyncio.Semaphore(max_creating)

    # ── subclass hooks ────────────────────────────────────────────

    async def _create_prewarmed(self) -> WorkspaceBase:
        """Build one initialised workspace bound to no user.

        Every input must be manager-level configuration — a buffered
        workspace is built before anyone knows who will get it.
        """
        raise NotImplementedError

    async def _adopt_prewarmed(self, workspace: WorkspaceBase) -> None:
        """Track a handed-out workspace as if built on demand.

        Called once, before the workspace's id is returned as a
        binding, so the manager's own cache can answer the
        ``get_workspace`` that follows.
        """
        raise NotImplementedError

    # ── lifecycle ─────────────────────────────────────────────────

    def _start_prewarm(self) -> None:
        """Fill the buffer. Returns at once; builds run in background."""
        self._refill()

    async def _stop_prewarm(self) -> None:
        """Drain the buffer, closing whatever it holds.

        In-flight builds are awaited rather than cancelled — a build
        killed halfway leaves an orphaned container or sandbox that
        nothing will ever reap.
        """
        self._prewarm = 0
        if self._prewarm_tasks:
            await asyncio.gather(
                *self._prewarm_tasks,
                return_exceptions=True,
            )
        ready = [
            f.result()
            for f in self._slots
            if f.done() and not f.cancelled() and f.exception() is None
        ]
        self._slots.clear()
        await asyncio.gather(
            *(ws.close() for ws in ready),
            return_exceptions=True,
        )

    # ── buffer ────────────────────────────────────────────────────

    def _refill(self) -> None:
        """Top the buffer back up to :attr:`_prewarm` slots."""
        while len(self._slots) < self._prewarm:
            future: asyncio.Future = (
                asyncio.get_running_loop().create_future()
            )
            self._slots.append(future)
            task = asyncio.create_task(self._fill_slot(future))
            self._prewarm_tasks.add(task)
            task.add_done_callback(self._prewarm_tasks.discard)

    async def _fill_slot(self, future: asyncio.Future) -> None:
        """Build one workspace and resolve ``future`` with it."""
        try:
            async with self._creating:
                workspace = await self._create_prewarmed()
        except Exception as e:
            # Drop the slot rather than immediately rebuilding, so a
            # provider that is down cannot spin a hot retry loop; the
            # next take refills. A slot already taken has a waiter
            # who needs to hear about the failure.
            if future in self._slots:
                self._slots.remove(future)
            if not future.done():
                future.set_exception(e)
            logger.warning(
                "%s: pre-warm build failed: %s",
                type(self).__name__,
                e,
            )
            return
        if future.done():  # cancelled while building
            await workspace.close()
            return
        future.set_result(workspace)

    async def _mint_workspace_id(self) -> str:
        """Hand out a buffered workspace and return its id.

        Falls through to the base — a plain fresh UUID naming a
        workspace still to be built — when pre-warming is off, or when
        the buffer is starved because recent builds failed.
        """
        if not self._prewarm:
            return await super()._mint_workspace_id()
        future = self._slots.popleft() if self._slots else None
        self._refill()
        if future is None:
            return await super()._mint_workspace_id()
        workspace = await future
        await self._adopt_prewarmed(workspace)
        return workspace.workspace_id
