# -*- coding: utf-8 -*-
"""Background sweep for stuck knowledge-document indexing jobs.

The indexing pipeline relies on two storage-level signals to keep
moving when something goes wrong:

- a *lease* per in-flight document — its ``lease_expires_at`` is the
  upper bound on how long a worker may sit on the document before
  another worker is allowed to take over;
- a *creation timestamp* on every ``pending`` record — used to catch
  documents that were never picked up by a worker (e.g. process died
  right after the upload endpoint persisted the record).

The sweeper periodically scans storage for both classes of stuck
records and re-enqueues them on the index-task channel.  Re-enqueue is
safe because the worker's CAS lease acquisition rejects duplicates,
so multiple nodes running their own sweeper does not produce double
processing.
"""
import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..._logging import logger
from .._bus_ops import enqueue_index_task

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import StorageBase


class IndexSweeper:
    """Periodically re-enqueues documents stuck in indexing.

    Lifecycle is wired into the app's lifespan: :meth:`start` schedules
    the background task and runs an immediate sweep so that documents
    left stuck by the previous process generation get picked up at
    once; :meth:`stop` cancels the loop on shutdown.
    """

    def __init__(
        self,
        storage: "StorageBase",
        message_bus: "MessageBus",
        interval: timedelta = timedelta(seconds=60),
        pending_grace: timedelta = timedelta(minutes=5),
    ) -> None:
        """Initialize the sweeper.

        Args:
            storage (`StorageBase`):
                Used to find stuck records and as the contract holder
                for the lease semantics.
            message_bus (`MessageBus`):
                The same bus the upload endpoint uses.  Re-enqueuing
                a document re-enters the worker pipeline, where the
                CAS lease acquisition decides whether to actually
                process or bail.
            interval (`timedelta`, defaults to ``60s``):
                How often the loop wakes up.  Roughly one order of
                magnitude shorter than the typical lease TTL — fast
                enough to recover from crashes within a few minutes,
                slow enough not to thrash storage.
            pending_grace (`timedelta`, defaults to ``5min``):
                A record may legitimately sit in ``pending`` while the
                bus push is still queued; only after this grace period
                do we treat the record as orphaned.
        """
        self._storage = storage
        self._bus = message_bus
        self._interval = interval
        self._pending_grace = pending_grace
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background sweep loop and run one immediate sweep."""
        if self._task is not None:
            return
        # Catch up from any state the previous generation left behind.
        await self._sweep_once()
        self._task = asyncio.create_task(
            self._loop(),
            name="kb-index-sweeper",
        )

    async def stop(self) -> None:
        """Cancel the sweep loop and wait for it to exit."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        """Run sweeps forever until cancelled."""
        interval_seconds = self._interval.total_seconds()
        while True:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 — keep the loop alive
                logger.exception("Sweep iteration failed")

    async def _sweep_once(self) -> None:
        """Find and re-enqueue every stuck document.

        De-duplication: a document showing up in both the
        expired-lease and orphan-pending queries (a record that was
        never picked up and whose lease pre-dates the grace period)
        is enqueued only once per sweep, by record id.
        """
        now = datetime.now()
        pending_threshold = now - self._pending_grace

        seen: set[str] = set()
        stuck = (
            await self._storage.list_knowledge_documents_with_expired_lease(
                now=now,
            )
        )
        orphans = await self._storage.list_knowledge_documents_pending_since(
            threshold=pending_threshold,
        )
        for record in (*stuck, *orphans):
            if record.id in seen:
                continue
            seen.add(record.id)
            try:
                await enqueue_index_task(
                    self._bus,
                    user_id=record.user_id,
                    knowledge_base_id=record.knowledge_base_id,
                    document_id=record.id,
                )
            except Exception:  # noqa: BLE001 — keep iterating
                logger.exception(
                    "Failed to re-enqueue document %s",
                    record.id,
                )

        if seen:
            logger.info("Re-enqueued %d stuck document(s)", len(seen))
