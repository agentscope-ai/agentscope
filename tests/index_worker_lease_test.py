# -*- coding: utf-8 -*-
"""Regression tests for :class:`IndexWorker.process` lease handling.

The pipeline must stop the moment the lease has been stolen by the
sweeper — otherwise the original worker and the worker that just took
over both write the same document into the vector store, producing
duplicate chunks (PR #1926 unresolved review #discussion_r3479544207).
"""
import asyncio
from datetime import timedelta
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.app._service._index_worker import IndexWorker


class _LeaseStorage:
    """Minimal storage stub recording lifecycle calls.

    Driven by a per-document ``renew_results`` queue so tests can stage
    a "renew returns True a few times, then False" pattern that mirrors
    a sweeper reaping a slow worker.
    """

    def __init__(self) -> None:
        self.acquire_returns: bool = True
        self.renew_results: list[bool] = []
        self.released: list[dict] = []
        self.status_updates: list[dict] = []
        self.renew_calls = 0

    async def acquire_knowledge_document_lease(
        self,
        **kwargs: Any,
    ) -> bool:
        """Return the staged ``acquire_returns`` flag."""
        del kwargs
        return self.acquire_returns

    async def renew_knowledge_document_lease(
        self,
        **kwargs: Any,
    ) -> bool:
        """Pop next staged renew result; default to ``True`` once drained."""
        del kwargs
        self.renew_calls += 1
        if not self.renew_results:
            return True
        return self.renew_results.pop(0)

    async def release_knowledge_document_lease(
        self,
        **kwargs: Any,
    ) -> None:
        """Record the release call so tests can assert it ran."""
        self.released.append(kwargs)

    async def update_knowledge_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        status: str,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Record the status transition for later assertion."""
        del user_id, knowledge_base_id, document_id
        self.status_updates.append(
            {
                "status": status,
                "error": error,
                "chunk_count": chunk_count,
            },
        )


class _SlowPipelineWorker(IndexWorker):
    """Replaces ``_run_pipeline`` with a long sleep so we can race the
    lease timer.

    The whole point of the regression is "what happens if a worker is
    *still in_progress* when its lease is taken away" — the only way to
    test that deterministically without standing up an embedding model
    and a vector store is to make the pipeline trivially long-running.
    """

    def __init__(self, storage: _LeaseStorage, pipeline_seconds: float):
        # Skip the real __init__ — we only need a handful of fields.
        self._storage = storage  # type: ignore[assignment]
        self._node_id = "test-node"
        self._lease_ttl = timedelta(seconds=10)
        self._sem = asyncio.Semaphore(4)
        # Renew quickly so a False result is visible within the test.
        self._renew_interval = timedelta(seconds=0.05)
        self._pipeline_seconds = pipeline_seconds
        self.pipeline_started = asyncio.Event()
        self.pipeline_cancelled = False
        self.pipeline_completed = False

    async def _run_pipeline(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Sleep ``pipeline_seconds`` so the test can race the heartbeat."""
        del user_id, knowledge_base_id, document_id
        self.pipeline_started.set()
        try:
            await asyncio.sleep(self._pipeline_seconds)
            self.pipeline_completed = True
        except asyncio.CancelledError:
            self.pipeline_cancelled = True
            raise


class IndexWorkerLeaseTest(IsolatedAsyncioTestCase):
    """Pipeline-vs-heartbeat race coverage."""

    async def test_lost_lease_cancels_pipeline_and_marks_error(self) -> None:
        """Renew returning False mid-pipeline must abort the pipeline.

        Otherwise the original worker keeps running while the new
        worker (that took over the lease) also runs — both end up
        inserting the same chunks into the vector store.
        """
        storage = _LeaseStorage()
        # First renew succeeds, second renew fails (sweeper stole it).
        storage.renew_results = [True, False]

        worker = _SlowPipelineWorker(storage, pipeline_seconds=5.0)
        # Bound the test so a regression hangs visibly rather than
        # silently passing.
        await asyncio.wait_for(
            worker.process("u", "kb", "doc-1"),
            timeout=3.0,
        )

        self.assertTrue(
            worker.pipeline_started.is_set(),
            "Pipeline never started.",
        )
        self.assertTrue(
            worker.pipeline_cancelled,
            "Pipeline was NOT cancelled after the lease was lost — "
            "this is the regression PR #1926 review flagged.",
        )
        self.assertFalse(
            worker.pipeline_completed,
            "Pipeline ran to completion despite the lost lease.",
        )

        # _mark_error must have recorded the lost-lease reason.
        errors = [u for u in storage.status_updates if u["status"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Lost lease", errors[0]["error"])

        # Release is still called (and is a safe no-op server-side).
        self.assertEqual(len(storage.released), 1)

    async def test_happy_path_cancels_heartbeat_and_releases(self) -> None:
        """Normal completion still tears the heartbeat down cleanly."""
        storage = _LeaseStorage()
        # Heartbeat always succeeds.
        storage.renew_results = []

        worker = _SlowPipelineWorker(storage, pipeline_seconds=0.05)
        await asyncio.wait_for(
            worker.process("u", "kb", "doc-ok"),
            timeout=2.0,
        )

        self.assertTrue(worker.pipeline_completed)
        self.assertFalse(worker.pipeline_cancelled)
        # No error update on the happy path.
        self.assertEqual(
            [u for u in storage.status_updates if u["status"] == "error"],
            [],
        )
        self.assertEqual(len(storage.released), 1)

    async def test_not_acquired_short_circuits(self) -> None:
        """When the lease is already held by another worker, do nothing."""
        storage = _LeaseStorage()
        storage.acquire_returns = False

        worker = _SlowPipelineWorker(storage, pipeline_seconds=5.0)
        await asyncio.wait_for(
            worker.process("u", "kb", "doc-locked"),
            timeout=1.0,
        )

        self.assertFalse(worker.pipeline_started.is_set())
        # No release either — we never held the lease.
        self.assertEqual(storage.released, [])
