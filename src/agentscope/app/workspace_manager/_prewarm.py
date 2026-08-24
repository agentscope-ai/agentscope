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
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from ..._logging import logger
from ...workspace import WorkspaceBase
from ._base import WorkspaceManagerBase

T = TypeVar("T", bound=WorkspaceBase)


class PrewarmConfig(BaseModel):
    """How many workspaces a manager keeps built ahead of demand."""

    size: int = Field(
        default=0,
        ge=0,
        description=(
            "Workspaces to keep built and idle, ready to hand to the "
            "next session that needs one. 0 disables pre-warming."
        ),
    )

    max_creating: int = Field(
        default=4,
        gt=0,
        description=(
            "Ceiling on builds running at once, so a burst of sessions "
            "queues instead of stampeding the provider."
        ),
    )


class WorkspacePrewarmMixin(WorkspaceManagerBase, Generic[T]):
    """A buffer of pre-built, unassigned workspaces.

    Mix in *before* :class:`WorkspaceManagerBase`, parameterised by the
    concrete workspace type, and implement
    :meth:`_create_prewarmed` and :meth:`_adopt_prewarmed`. Drive the
    buffer from the manager's ``__aenter__`` / ``__aexit__`` via
    :meth:`_start_prewarm` and :meth:`_stop_prewarm`.

    No lock guards the buffer: every mutation of :attr:`_slots` and
    :attr:`_built` runs to completion without an intervening ``await``,
    so on asyncio's single thread no other coroutine can observe a
    half-applied change.
    """

    def __init__(self, prewarm: PrewarmConfig | None = None) -> None:
        """Size the buffer and the build concurrency.

        Args:
            prewarm (`PrewarmConfig | None`, optional):
                Buffer settings. ``None`` disables pre-warming.
        """
        self._prewarm = prewarm or PrewarmConfig()
        self._slots: deque[asyncio.Future] = deque()
        self._prewarm_tasks: set[asyncio.Task] = set()
        self._creating = asyncio.Semaphore(self._prewarm.max_creating)
        # Built but not yet handed to the manager's cache. Whatever is
        # still here at shutdown — never taken, or taken by a caller
        # that was cancelled mid-await — would otherwise leak.
        self._built: set[T] = set()

    # ── subclass hooks ────────────────────────────────────────────

    async def _create_prewarmed(self) -> T:
        """Build one initialised workspace bound to no user.

        Every input must be manager-level configuration — a buffered
        workspace is built before anyone knows who will get it.
        """
        raise NotImplementedError

    async def _adopt_prewarmed(self, workspace: T) -> None:
        """Track a handed-out workspace as if built on demand.

        Called once, before the workspace's id is returned as a
        binding, so the manager's own cache can answer the
        ``get_workspace`` that follows.
        """
        raise NotImplementedError

    async def _dispose_prewarmed(self, workspace: T) -> None:
        """Discard a workspace nobody ever claimed.

        Its id was never persisted, so nothing will reattach to it and
        nothing else will reap it. Providers whose ``close`` preserves
        the sandbox for a later reattach — E2B pauses rather than
        kills — must override this to delete it outright.
        """
        await workspace.close()

    # ── lifecycle ─────────────────────────────────────────────────

    def _start_prewarm(self) -> None:
        """Fill the buffer. Returns at once; builds run in background."""
        self._refill()

    async def _stop_prewarm(self) -> None:
        """Drain the buffer, closing every workspace still unclaimed.

        In-flight builds are awaited rather than cancelled — a build
        killed halfway leaves an orphaned container or sandbox that
        nothing will ever reap.
        """
        self._prewarm = PrewarmConfig()
        if self._prewarm_tasks:
            await asyncio.gather(
                *self._prewarm_tasks,
                return_exceptions=True,
            )
        self._slots.clear()
        unclaimed, self._built = self._built, set()
        await asyncio.gather(
            *(self._dispose_prewarmed(ws) for ws in unclaimed),
            return_exceptions=True,
        )

    # ── buffer ────────────────────────────────────────────────────

    def _refill(self) -> None:
        """Top the buffer back up to :attr:`PrewarmConfig.size` slots."""
        while len(self._slots) < self._prewarm.size:
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._slots.append(future)
            task = asyncio.create_task(self._fill_slot(future))
            self._prewarm_tasks.add(task)
            task.add_done_callback(self._prewarm_tasks.discard)

    async def _fill_slot(self, future: asyncio.Future) -> None:
        """Build one workspace and resolve ``future`` with it.

        Every path resolves the future — a slot left pending would hang
        its taker for good, so bookkeeping that could raise stays
        inside the guarded block.
        """
        try:
            async with self._creating:
                workspace = await self._create_prewarmed()
            self._built.add(workspace)
        except Exception as e:
            # Drop the slot rather than rebuild straight away, so a
            # provider that is down cannot spin a hot retry loop; the
            # next take refills. A slot already taken keeps its waiter,
            # who falls back to an ordinary id.
            if future in self._slots:
                # Unclaimed, and now unreachable — nobody will ever
                # retrieve an exception set here.
                self._slots.remove(future)
                future.cancel()
            elif not future.done():
                future.set_exception(e)
            logger.warning(
                "%s: pre-warm build failed: %s",
                type(self).__name__,
                e,
            )
            return
        if future.done():  # cancelled while building
            self._built.discard(workspace)
            await self._dispose_prewarmed(workspace)
            return
        future.set_result(workspace)

    async def _mint_workspace_id(self) -> str:
        """Hand out a buffered workspace and return its id.

        Falls through to the base — a plain fresh id naming a workspace
        still to be built — when pre-warming is off, when the buffer is
        starved because recent builds failed, or when the build behind
        this slot is the one that failed. A pre-warm failure must not
        take the session down with it: without pre-warming the session
        would simply have built its own workspace later.
        """
        if not self._prewarm.size:
            return await super()._mint_workspace_id()
        future = self._slots.popleft() if self._slots else None
        self._refill()
        if future is None:
            return await super()._mint_workspace_id()
        try:
            workspace = await future
        except Exception:
            # Only a failed build reaches the taker as an exception;
            # ``_fill_slot`` cancels unclaimed slots instead. So a
            # ``CancelledError`` here is the caller's own, and must
            # not be turned into a workspace nobody asked for.
            return await super()._mint_workspace_id()
        try:
            await self._adopt_prewarmed(workspace)
        except BaseException:
            # The cache never took it and the caller is gone, so
            # nothing is left holding it — reclaim it now rather than
            # waiting for shutdown.
            self._built.discard(workspace)
            await self._dispose_prewarmed(workspace)
            raise
        self._built.discard(workspace)
        return workspace.workspace_id
