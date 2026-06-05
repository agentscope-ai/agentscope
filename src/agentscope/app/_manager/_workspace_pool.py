# -*- coding: utf-8 -*-
"""Generic async workspace pool with pre-warming and FIFO scheduling.

Designed according to the Pooling Design specification:

* **Lifecycle**: CREATING → POOLED → ACTIVE → RESETTING → POOLED / DESTROYED
* **Scheduling**: ``min_idle``/``max_idle``/``total``/``create_batch_size``
  govern pre-warming and capacity.
* **Maintenance**: A unified background ``_maintain_loop`` keeps the idle
  count within the ``[min_idle, max_idle]`` band.  When idle count drops
  below ``min_idle``, new instances are created up to ``max_idle``.  When
  idle count exceeds ``max_idle``, excess instances are drained and
  destroyed.  Each workspace also tracks its reuse count; once
  ``max_reuse`` is reached the instance is destroyed instead of recycled.
  Health checks are performed inline at ``acquire`` and ``release`` time.
* **Overflow fallback**: When the pool is at its ``total`` capacity and
  no idle entries are available, ``acquire`` creates an *overflow*
  workspace directly via the factory — bypassing the capacity limit so
  callers are never blocked indefinitely.  On ``release``, overflow
  entries are absorbed into the pool if headroom exists, or destroyed
  outright.
* **FIFO**: Idle workspaces are handed out in the order they became
  available (via :class:`asyncio.Queue`).
* **Cost control**: Idle workspaces are kept in a *paused* state via an
  optional ``pause_fn`` / ``resume_fn`` pair so backends that bill by
  uptime (e.g. E2B) do not accumulate charges while instances sit in
  the pool.
"""

import asyncio
import enum
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from ..._logging import logger

T = TypeVar("T")  # concrete workspace type


# ── exceptions ─────────────────────────────────────────────────────


class PoolExhaustedError(TimeoutError):
    """Raised when :meth:`WorkspacePool.acquire` cannot obtain a workspace
    within the caller-specified timeout."""


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
    overflow: bool = False


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
            count drops below this, the background replenish loop creates
            new instances.
        max_idle: Target idle count after batch replenishment.
        total: Hard cap on total managed instances (idle + active).
        create_batch_size: Maximum concurrent ``factory()`` calls per
            replenishment batch.
        max_reuse: Maximum times a workspace can be recycled before
            destruction. ``0`` means unlimited.
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

        # FIFO queue of idle (POOLED) entries ready for checkout.
        self._idle: asyncio.Queue[PooledEntry[T]] = asyncio.Queue()
        # All managed entries (any state except DESTROYED).
        self._all: dict[int, PooledEntry[T]] = {}
        self._lock = asyncio.Lock()

        # Background tasks
        self._maintain_task: asyncio.Task | None = None
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
        """Start background tasks.

        Pre-warming to ``min_idle`` happens asynchronously in the
        background maintain loop — ``start`` returns immediately.
        The first :meth:`acquire` call may block until the initial
        batch of workspaces is ready.
        """
        self._stopping = False
        if self._maintain_task is None:
            self._maintain_task = asyncio.create_task(
                self._maintain_loop(),
            )

    async def stop(self) -> None:
        """Stop background tasks and destroy every managed instance.

        The maintain task is allowed to finish naturally (rather than
        being cancelled) so that any in-flight ``factory()`` call
        completes and its workspace is cleaned up properly.
        """
        self._stopping = True

        # Wait for the maintain task to finish naturally.  The loop
        # checks ``_stopping`` at the top of every iteration and after
        # each sleep, so it exits promptly.  ``_create_one`` also checks
        # ``_stopping`` after ``factory()`` returns, ensuring any
        # in-flight workspace is cleaned up rather than pooled.
        if self._maintain_task is not None:
            try:
                await self._maintain_task
            except Exception:
                logger.exception(
                    "WorkspacePool: maintain task raised during stop",
                )
            self._maintain_task = None

        # At this point no background task can put new entries into
        # the idle queue.  Drain it first (so a concurrent acquire()
        # does not pick up stale entries), then collect and destroy
        # every tracked entry.
        while not self._idle.empty():
            try:
                self._idle.get_nowait()
            except asyncio.QueueEmpty:
                break

        async with self._lock:
            entries = list(self._all.values())
            self._all.clear()

        await asyncio.gather(
            *(self._safe_destroy(e) for e in entries),
            return_exceptions=True,
        )

    # ── checkout / checkin ──────────────────────────────────────────

    async def acquire(
        self,
        timeout: float | None = None,
    ) -> PooledEntry[T]:
        """Obtain an idle workspace from the pool (FIFO).

        The method first tries to dequeue an idle entry without blocking.
        If the queue is empty *and* the pool has reached its ``total``
        capacity cap, it falls back to creating an **overflow** workspace
        directly via the factory (bypassing the pool's capacity limit).
        Overflow entries are returned in ``ACTIVE`` state with
        ``overflow=True``; on :meth:`release`, they are absorbed into
        the pool if headroom exists, or destroyed otherwise.

        If the queue is empty but the pool still has capacity headroom,
        the method blocks on the queue (up to ``timeout``) waiting for
        the background maintain loop to create new entries.

        Each entry is health-checked before being handed out.  If the
        check fails the entry is destroyed and the next one is tried,
        so the caller is guaranteed to receive a healthy, *running*
        workspace.

        Args:
            timeout: Maximum seconds to wait for a healthy workspace.
                ``None`` (the default) means wait indefinitely.

        Returns:
            A :class:`PooledEntry` in ``ACTIVE`` state.

        Raises:
            PoolExhaustedError: If no healthy workspace becomes
                available within ``timeout`` seconds.
            RuntimeError: If the pool is stopping.
        """
        loop = asyncio.get_event_loop()
        deadline = (loop.time() + timeout) if timeout is not None else None

        while True:
            if self._stopping:
                raise RuntimeError("WorkspacePool is stopping")

            # ── fast path: try to grab an idle entry without blocking ──
            try:
                entry = self._idle.get_nowait()
            except asyncio.QueueEmpty as queue_empty_exc:
                # No idle entry available right now.
                if self.total_managed >= self._total:
                    # Pool is at capacity — fall back to overflow creation
                    # so the caller is not blocked indefinitely.
                    return await self._create_overflow()

                # Pool still has headroom — wait for the maintain loop
                # to supply new entries.
                remaining: float | None = None
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise PoolExhaustedError(
                            f"No workspace available within {timeout}s "
                            f"(total={self._total}, "
                            f"idle={self.idle_count})",
                        ) from queue_empty_exc

                try:
                    entry = await asyncio.wait_for(
                        self._idle.get(),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError as timeout_exc:
                    raise PoolExhaustedError(
                        f"No workspace available within {timeout}s "
                        f"(total={self._total}, "
                        f"idle={self.idle_count})",
                    ) from timeout_exc

            # ── resume + health-check the dequeued entry ───────────
            if self._resume_fn is not None:
                try:
                    await self._resume_fn(entry.workspace)
                except Exception:
                    logger.exception(
                        "WorkspacePool: resume failed on acquire, destroying",
                    )
                    await self._destroy_entry(entry)
                    continue

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

    async def release(self, entry: PooledEntry[T]) -> None:
        """Return a workspace to the pool after use.

        The workspace is reset and health-checked while still running.
        If healthy (and below ``max_reuse``), it is paused via
        ``pause_fn`` and placed back into the idle queue; otherwise it
        is destroyed.

        For **overflow** entries (created outside the pool's ``total``
        cap), the pool first checks whether capacity headroom exists.
        If so the entry is absorbed into ``_all`` and follows the
        normal reset → health-check → pause → re-pool path.  If the
        pool is still at capacity the entry is destroyed immediately.

        Args:
            entry: The entry previously obtained via :meth:`acquire`.
        """
        if self._stopping:
            if entry.overflow:
                await self._safe_destroy(entry)
            else:
                await self._destroy_entry(entry)
            return

        # ── overflow entry handling ────────────────────────────────
        if entry.overflow:
            async with self._lock:
                if self.total_managed < self._total:
                    # Absorb into the pool — register in _all and
                    # continue with the normal release flow below.
                    entry.overflow = False
                    self._all[id(entry)] = entry
                    logger.info(
                        "WorkspacePool: absorbing overflow workspace "
                        "into pool (total_managed=%d, total=%d)",
                        self.total_managed,
                        self._total,
                    )
                else:
                    # Still at capacity — destroy without reset.
                    logger.info(
                        "WorkspacePool: pool still at capacity, "
                        "destroying overflow workspace",
                    )
            if entry.overflow:
                # Was not absorbed (still at capacity).
                await self._safe_destroy(entry)
                return

        # Check max reuse limit.
        if self._max_reuse > 0 and entry.reuse_count >= self._max_reuse:
            logger.info(
                "WorkspacePool: max reuse (%d) reached, destroying",
                self._max_reuse,
            )
            await self._destroy_entry(entry)
            return

        entry.state = PooledState.RESETTING
        try:
            await self._reset_fn(entry.workspace)
        except Exception:
            logger.exception("WorkspacePool: reset failed, destroying")
            await self._destroy_entry(entry)
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
                return

        entry.state = PooledState.POOLED
        if self._stopping:
            await self._destroy_entry(entry)
            return
        await self._idle.put(entry)

    # ── pool maintenance ─────────────────────────────────────────────

    async def _maintain_loop(self) -> None:
        """Long-lived background task that maintains the pool water level.

        Runs an initial warm-up to ``min_idle``, then periodically
        checks whether the idle count has drifted outside the
        ``[min_idle, max_idle]`` band and adjusts accordingly:

        * **Below ``min_idle``**: replenish up to ``max_idle``.
        * **Above ``max_idle``**: drain and destroy excess entries.

        No external signal is needed — the loop is fully self-driven.
        """
        # Initial warm-up: fill to min_idle.
        try:
            await self._replenish(target=self._min_idle)
        except Exception:
            logger.exception("WorkspacePool: initial warm-up failed")

        # Periodic polling loop.
        while not self._stopping:
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return
            if self._stopping:
                return

            idle = self.idle_count

            if idle < self._min_idle and self.total_managed < self._total:
                try:
                    await self._replenish(target=self._max_idle)
                except Exception:
                    logger.exception(
                        "WorkspacePool: replenish cycle failed",
                    )
            elif idle > self._max_idle:
                try:
                    await self._shrink(target=self._max_idle)
                except Exception:
                    logger.exception(
                        "WorkspacePool: shrink cycle failed",
                    )

    async def _replenish(self, target: int) -> None:
        """Create new instances until idle count reaches ``target``.

        Respects the ``total`` cap and creates in batches of
        ``create_batch_size``.
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

    async def _shrink(self, target: int) -> None:
        """Drain and destroy excess idle entries until idle count
        reaches ``target``.

        Entries are removed from the FIFO queue and destroyed one by
        one.  The loop stops as soon as the idle count is at or below
        the target, or the queue is empty.
        """
        while not self._stopping and self.idle_count > target:
            try:
                entry = self._idle.get_nowait()
            except asyncio.QueueEmpty:
                break
            logger.info(
                "WorkspacePool: shrinking pool — destroying excess idle "
                "workspace (idle_count was %d, target %d)",
                self.idle_count + 1,  # +1 because we just dequeued
                target,
            )
            await self._destroy_entry(entry)

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
                await self._destroy_created_entry(entry, ws)
                raise

        if self._stopping:
            await self._destroy_created_entry(entry, ws)
            return

        entry.workspace = ws
        entry.state = PooledState.POOLED
        await self._idle.put(entry)

    async def _create_overflow(self) -> PooledEntry[T]:
        """Create an overflow workspace directly via the factory.

        Called by :meth:`acquire` when the pool has reached its
        ``total`` capacity and no idle entries are available.  The
        created entry is **not** registered in ``_all`` and therefore
        does not count towards the ``total`` cap.

        On :meth:`release`, the entry will be absorbed into the pool
        if capacity permits, or destroyed outright.

        Returns:
            A :class:`PooledEntry` in ``ACTIVE`` state with
            ``overflow=True``.
        """
        logger.warning(
            "WorkspacePool: pool at capacity (total=%d), "
            "falling back to overflow creation",
            self._total,
        )
        ws = await self._factory()
        entry = PooledEntry[T](
            workspace=ws,
            state=PooledState.ACTIVE,
            reuse_count=1,
            overflow=True,
        )
        return entry

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
        if entry.workspace is None:
            return
        try:
            await self._close_fn(entry.workspace)
        except Exception:
            logger.exception("WorkspacePool: close_fn failed")
