# -*- coding: utf-8 -*-
"""Generic async workspace pool with pre-warming, health checks, and FIFO
scheduling.

Designed according to the Pooling Design specification:

* **Lifecycle**: CREATING → POOLED → ACTIVE → RESETTING → POOLED / DESTROYED
* **Scheduling**: ``min_idle``/``max_idle``/``total``/``create_batch_size``
  govern pre-warming and capacity.
* **Maintenance**: Each workspace tracks its reuse count; once
  ``max_reuse`` is reached the instance is destroyed instead of recycled.
  A background health-check loop probes idle instances and evicts unhealthy
  ones.
* **FIFO**: Idle workspaces are handed out in the order they became
  available (via :class:`asyncio.Queue`).
* **Cost control**: Idle workspaces are kept in a *paused* state via an
  optional ``pause_fn`` / ``resume_fn`` pair so backends that bill by
  uptime (e.g. E2B) do not accumulate charges while instances sit in
  the pool.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from ..._logging import logger

T = TypeVar("T")  # concrete workspace type


# ── instance state machine ──────────────────────────────────────────


class PooledState(enum.Enum):
    """Lifecycle states for a pooled workspace instance."""

    CREATING = "creating"
    POOLED = "pooled"
    ACTIVE = "active"
    RESETTING = "resetting"
    DESTROYED = "destroyed"


@dataclass
class PooledEntry(Generic[T]):
    """Bookkeeping wrapper around a single pooled workspace."""

    workspace: T
    state: PooledState = PooledState.CREATING
    reuse_count: int = 0
    created_at: float = field(default_factory=time.monotonic)


# ── the pool ────────────────────────────────────────────────────────


class WorkspacePool(Generic[T]):
    """Pre-warming async pool for heavy-to-create workspaces.

    Type parameter ``T`` is the concrete workspace type (e.g.
    :class:`E2BWorkspace`).

    Args:
        factory: ``async () -> T`` — creates **and initializes** a fresh
            workspace instance. Must return a live, ready-to-use workspace.
        reset_fn: ``async (T) -> None`` — resets the workspace to a clean
            state after use. Should restart the gateway, wipe files, etc.
            Called while the workspace is still *running* (before pause).
        health_check_fn: ``async (T) -> bool`` — returns ``True`` if the
            workspace is healthy and can be re-pooled after reset.
            Called while the workspace is *running*.
        close_fn: ``async (T) -> None`` — permanently destroys the workspace.
        pause_fn: ``async (T) -> None`` — suspends the workspace to stop
            billing.  Called when the workspace enters the POOLED state.
            ``None`` means idle workspaces stay running (suitable for
            backends without per-uptime billing).
        resume_fn: ``async (T) -> None`` — brings a paused workspace back
            to a running state.  Called when the workspace leaves the
            POOLED state for ACTIVE.  ``None`` means no resume is needed.
        min_idle: Minimum number of idle instances to maintain. When idle
            count drops below this on ``acquire``, a batch replenishment
            is triggered.
        max_idle: Target idle count after batch replenishment.
        total: Hard cap on total managed instances (idle + active).
        create_batch_size: Maximum concurrent ``factory()`` calls per
            replenishment batch.
        max_reuse: Maximum times a workspace can be recycled before
            destruction. ``0`` means unlimited.
        health_check_interval: Seconds between background health-check
            sweeps over idle instances.
    """

    def __init__(
        self,
        *,
        factory: Callable[[], Awaitable[T]],
        reset_fn: Callable[[T], Awaitable[None]],
        health_check_fn: Callable[[T], Awaitable[bool]],
        close_fn: Callable[[T], Awaitable[None]],
        pause_fn: Callable[[T], Awaitable[None]] | None = None,
        resume_fn: Callable[[T], Awaitable[None]] | None = None,
        min_idle: int = 1,
        max_idle: int = 3,
        total: int = 10,
        create_batch_size: int = 2,
        max_reuse: int = 0,
        health_check_interval: float = 60.0,
    ) -> None:
        self._factory = factory
        self._reset_fn = reset_fn
        self._health_check_fn = health_check_fn
        self._close_fn = close_fn
        self._pause_fn = pause_fn
        self._resume_fn = resume_fn

        self._min_idle = min_idle
        self._max_idle = max_idle
        self._total = total
        self._create_batch_size = create_batch_size
        self._max_reuse = max_reuse
        self._health_check_interval = health_check_interval

        # FIFO queue of idle (POOLED) entries ready for checkout.
        self._idle: asyncio.Queue[PooledEntry[T]] = asyncio.Queue()
        # All managed entries (any state except DESTROYED).
        self._all: dict[int, PooledEntry[T]] = {}
        self._lock = asyncio.Lock()

        # Background tasks
        self._health_task: asyncio.Task | None = None
        self._replenish_tasks: set[asyncio.Task[None]] = set()
        self._replenish_lock = asyncio.Lock()
        self._stopping = False

    # ── pool size helpers ───────────────────────────────────────────

    @property
    def total_managed(self) -> int:
        """Number of non-destroyed instances currently tracked."""
        return len(self._all)

    @property
    def idle_count(self) -> int:
        """Approximate number of POOLED instances in the queue."""
        return self._idle.qsize()

    # ── lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Start background tasks and pre-warm the pool to ``min_idle``."""
        self._stopping = False
        if self._health_task is None:
            self._health_task = asyncio.create_task(
                self._health_check_loop(),
            )
        # Initial warm-up: fill to min_idle.
        async with self._replenish_lock:
            await self._replenish(target=self._min_idle)

    async def stop(self) -> None:
        """Cancel background tasks and destroy every managed instance."""
        self._stopping = True

        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
            self._health_task = None

        # Do not cancel replenish tasks while factory() may be inside a
        # workspace initialize call.  Wait for them to reach their stopping
        # checks so any workspace created during shutdown is closed normally.
        replenish_tasks = list(self._replenish_tasks)
        if replenish_tasks:
            await asyncio.gather(*replenish_tasks, return_exceptions=True)
            self._replenish_tasks.clear()

        # Drain the idle queue so no awaiter gets stale entries.
        while not self._idle.empty():
            try:
                self._idle.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Destroy every tracked entry.
        async with self._lock:
            entries = list(self._all.values())
            self._all.clear()

        await asyncio.gather(
            *(self._safe_destroy(e) for e in entries),
            return_exceptions=True,
        )

    # ── checkout / checkin ──────────────────────────────────────────

    async def acquire(self) -> PooledEntry[T]:
        """Obtain an idle workspace from the pool (FIFO).

        If the idle count drops below ``min_idle`` (and ``total`` cap
        allows), a background batch replenishment is kicked off.  The
        caller always blocks on the queue until an entry is available.

        When ``resume_fn`` is configured, the entry is resumed before
        the health check.  Each entry is health-checked before being
        handed out.  If the check fails the entry is destroyed and the
        next one is tried, so the caller is guaranteed to receive a
        healthy, *running* workspace (or block until one becomes
        available).

        Returns:
            A :class:`PooledEntry` in ``ACTIVE`` state.
        """
        while True:
            if self._stopping:
                raise RuntimeError("WorkspacePool is stopping")

            # Trigger replenishment if needed (non-blocking background task).
            if (
                self.idle_count < self._min_idle
                and self.total_managed < self._total
            ):
                self._trigger_replenish()

            # Block until an idle entry is available.
            entry = await self._idle.get()

            # Resume first so the health check can reach the workspace.
            if self._resume_fn is not None:
                try:
                    await self._resume_fn(entry.workspace)
                except Exception:
                    logger.exception(
                        "WorkspacePool: resume failed on acquire, destroying",
                    )
                    await self._destroy_entry(entry)
                    self._trigger_replenish()
                    continue

            # Health-check before handing out — the entry may have
            # degraded between the last background sweep and now.
            try:
                healthy = await self._health_check_fn(entry.workspace)
            except Exception:
                logger.warning(
                    "WorkspacePool: acquire health-check raised, "
                    "treating as unhealthy",
                )
                healthy = False

            if healthy:
                entry.state = PooledState.ACTIVE
                entry.reuse_count += 1
                return entry

            # Unhealthy — destroy and loop to try the next entry.
            logger.warning(
                "WorkspacePool: acquired unhealthy instance, destroying "
                "and retrying",
            )
            await self._destroy_entry(entry)
            self._trigger_replenish()

    async def release(self, entry: PooledEntry[T]) -> None:
        """Return a workspace to the pool after use.

        The workspace is reset and health-checked while still running.
        If healthy (and below ``max_reuse``), it is paused via
        ``pause_fn`` and placed back into the idle queue; otherwise it
        is destroyed.

        Args:
            entry: The entry previously obtained via :meth:`acquire`.
        """
        if self._stopping:
            await self._destroy_entry(entry)
            return

        # Check max reuse limit.
        if self._max_reuse > 0 and entry.reuse_count >= self._max_reuse:
            logger.info(
                "WorkspacePool: max reuse (%d) reached, destroying",
                self._max_reuse,
            )
            await self._destroy_entry(entry)
            self._trigger_replenish()
            return

        entry.state = PooledState.RESETTING
        try:
            await self._reset_fn(entry.workspace)
        except Exception:
            logger.exception("WorkspacePool: reset failed, destroying")
            await self._destroy_entry(entry)
            self._trigger_replenish()
            return

        # Health check after reset (workspace is still running here).
        try:
            healthy = await self._health_check_fn(entry.workspace)
        except Exception:
            logger.exception(
                "WorkspacePool: health check after reset failed",
            )
            healthy = False

        if not healthy:
            logger.warning(
                "WorkspacePool: unhealthy after reset, destroying",
            )
            await self._destroy_entry(entry)
            self._trigger_replenish()
            return

        # Pause before returning to the idle queue to stop billing.
        if self._pause_fn is not None:
            try:
                await self._pause_fn(entry.workspace)
            except Exception:
                logger.exception(
                    "WorkspacePool: pause failed after reset, destroying",
                )
                await self._destroy_entry(entry)
                self._trigger_replenish()
                return

        entry.state = PooledState.POOLED
        if self._stopping:
            await self._destroy_entry(entry)
            return
        await self._idle.put(entry)

    # ── replenishment ───────────────────────────────────────────────

    def _trigger_replenish(self) -> None:
        """Kick off a background replenishment if one is not already running.

        Uses ``_replenish_lock`` to guarantee at most one concurrent
        replenishment.  If the lock is already held (i.e. a
        replenishment is in progress), this call is a no-op — the
        running replenishment will re-evaluate the idle count on its
        own.
        """
        if self._stopping:
            return

        task = asyncio.create_task(self._guarded_replenish())
        self._replenish_tasks.add(task)

        def _on_done(done: asyncio.Task[None]) -> None:
            self._replenish_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("WorkspacePool: replenish task failed")

        task.add_done_callback(_on_done)

    async def _guarded_replenish(self) -> None:
        """Acquire the replenish lock and run ``_replenish``.

        If the lock is already held, return immediately — there is
        no point queuing up a second replenishment because the one
        that holds the lock will keep looping until ``idle_count``
        reaches ``max_idle`` (or ``total`` is exhausted).
        """
        if self._stopping:
            return
        if self._replenish_lock.locked():
            return
        async with self._replenish_lock:
            if self._stopping:
                return
            await self._replenish(target=self._max_idle)

    async def _replenish(self, target: int) -> None:
        """Create new instances until idle count reaches ``target``.

        Respects the ``total`` cap and creates in batches of
        ``create_batch_size``.

        Must be called while holding ``_replenish_lock``.
        """
        while not self._stopping and self.idle_count < target:
            # How many can we still create?
            async with self._lock:
                headroom = self._total - self.total_managed
            if headroom <= 0:
                break

            batch = min(
                self._create_batch_size,
                headroom,
                target - self.idle_count,
            )
            if batch <= 0:
                break

            tasks = [self._create_one() for _ in range(batch)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    logger.exception(
                        "WorkspacePool: factory() failed during replenish: %s",
                        r,
                    )

    async def _create_one(self) -> None:
        """Create a single workspace via factory, pause it, and enqueue."""
        entry = PooledEntry[T](
            workspace=None,  # type: ignore[arg-type]
            state=PooledState.CREATING,
        )
        async with self._lock:
            if self._stopping or self.total_managed >= self._total:
                return
            oid = id(entry)
            self._all[oid] = entry

        try:
            ws = await self._factory()
        except Exception:
            async with self._lock:
                self._all.pop(id(entry), None)
            raise

        if self._stopping:
            await self._destroy_created_entry(entry, ws)
            return

        # Pause the freshly created workspace before pooling it.
        if self._pause_fn is not None:
            try:
                await self._pause_fn(ws)
            except Exception:
                logger.exception(
                    "WorkspacePool: pause after factory failed, destroying",
                )
                async with self._lock:
                    self._all.pop(id(entry), None)
                # Best-effort close — workspace was created but cannot
                # be paused, so destroy it outright.
                try:
                    await self._close_fn(ws)
                except Exception:
                    logger.exception("WorkspacePool: close_fn failed")
                raise

        if self._stopping:
            await self._destroy_created_entry(entry, ws)
            return

        entry.workspace = ws
        entry.state = PooledState.POOLED
        await self._idle.put(entry)

    # ── health-check loop ───────────────────────────────────────────

    async def _health_check_loop(self) -> None:
        """Periodically probe idle instances and evict unhealthy ones.

        When ``pause_fn`` is configured, idle instances are in a
        *paused* (suspended) state — they consume no resources and
        cannot degrade.  The health check is therefore skipped
        entirely: ``acquire`` already performs a post-resume health
        check, which is sufficient.  This avoids the prohibitive cost
        of resume → probe → pause round-trips on every sweep tick
        for backends like E2B that bill by uptime.
        """
        if self._pause_fn is not None:
            # Nothing to do — idle entries are paused, acquire will
            # health-check after resume.
            return

        while True:
            try:
                await asyncio.sleep(self._health_check_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._health_check_sweep()
            except Exception:
                logger.exception("WorkspacePool: health-check sweep failed")

    async def _health_check_sweep(self) -> None:
        """One pass: check idle instances one-at-a-time.

        Only called when ``pause_fn`` is ``None`` (i.e. idle entries
        stay *running*).  When entries are paused, the background loop
        exits immediately and this method is never invoked.

        Instead of draining the entire idle queue upfront (which would
        block concurrent ``acquire`` callers for the full sweep
        duration), we pop one entry, check it, and immediately put it
        back (or destroy it) before touching the next one.  This way an
        ``acquire`` waiting on the queue can proceed as soon as the
        first healthy entry is returned, rather than waiting for every
        entry to be probed.

        We snapshot the queue size at the start so we only inspect
        entries that were idle when the sweep began — entries added
        mid-sweep (by ``release`` or ``_replenish``) are left for the
        next cycle.
        """
        to_check = self._idle.qsize()
        destroyed_count = 0

        for _ in range(to_check):
            try:
                entry = self._idle.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                healthy = await self._health_check_fn(entry.workspace)
            except Exception:
                logger.warning(
                    "WorkspacePool: health-check probe raised, "
                    "treating as unhealthy",
                )
                healthy = False

            if healthy:
                # Put it back immediately so acquire() can grab it.
                await self._idle.put(entry)
            else:
                logger.warning(
                    "WorkspacePool: idle instance unhealthy, destroying",
                )
                await self._destroy_entry(entry)
                destroyed_count += 1

        if destroyed_count > 0:
            self._trigger_replenish()

    # ── destruction helpers ─────────────────────────────────────────

    async def _destroy_entry(self, entry: PooledEntry[T]) -> None:
        """Destroy a single entry and remove it from tracking."""
        entry.state = PooledState.DESTROYED
        async with self._lock:
            self._all.pop(id(entry), None)
        await self._safe_destroy(entry)

    async def _destroy_created_entry(
        self,
        entry: PooledEntry[T],
        workspace: T,
    ) -> None:
        """Destroy an entry whose factory completed during shutdown."""
        entry.workspace = workspace
        entry.state = PooledState.DESTROYED
        async with self._lock:
            self._all.pop(id(entry), None)
        await self._safe_destroy(entry)

    async def _safe_destroy(self, entry: PooledEntry[T]) -> None:
        """Call close_fn, swallowing exceptions."""
        entry.state = PooledState.DESTROYED
        if entry.workspace is None:
            return
        try:
            await self._close_fn(entry.workspace)
        except Exception:
            logger.exception("WorkspacePool: close_fn failed")
