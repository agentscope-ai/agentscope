# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Cancellation compensation for KB collection creation.

``create_knowledge_base`` allocates the vector-store collection before the
storage write, with a compensating delete on failure. The compensation used
to catch only ``Exception``, so a cancellation of ``upsert_knowledge_base``
(a ``CancelledError``, a ``BaseException``) leaked the collection: the
vector store kept a collection no record pointed at, and nothing sweeps
collections (the sweeper scans records, not the store) — the same class of
leak #3651 fixed for source blobs.
"""
import asyncio
from typing import Any

from agentscope.app.rag.knowledge_base_manager._collection_per_kb import (
    CollectionPerKbManager,
)
from agentscope.app.storage import EmbeddingModelConfig

import unittest


class _FakeVectorStore:
    """Records collection calls; can hang inside the delete."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.delete_started = asyncio.Event()
        self.delete_release: asyncio.Event | None = None

    async def create_collection(self, name: str, dimensions: int) -> None:
        self.created.append(name)

    async def delete_collection(self, name: str) -> None:
        self.deleted.append(name)
        if self.delete_release is not None:
            self.delete_started.set()
            await self.delete_release.wait()


class _HangingUpsertStorage:
    """``upsert_knowledge_base`` that hangs until cancelled."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self._failure = failure

    async def upsert_knowledge_base(self, user_id: str, record: Any) -> Any:
        if self._failure is not None:
            raise self._failure
        await asyncio.Event().wait()


def _embedding_config() -> EmbeddingModelConfig:
    return EmbeddingModelConfig(
        type="openai_credential",
        credential_id="cred-1",
        model="text-embedding-3-small",
        dimensions=1,
    )


class CreateKnowledgeBaseCancellationTest(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_cancelled_upsert_drops_orphan_collection(self) -> None:
        """A cancelled storage write must drop the allocated collection."""
        vector_store = _FakeVectorStore()
        manager = CollectionPerKbManager(_HangingUpsertStorage(), vector_store)

        task = asyncio.create_task(
            manager.create_knowledge_base(
                "user-1",
                "kb",
                "",
                _embedding_config(),
            )
        )
        # let the task reach the hanging upsert
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(len(vector_store.created), 1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=5)

        self.assertEqual(vector_store.deleted, vector_store.created)

    async def test_failed_upsert_still_drops_orphan_collection(self) -> None:
        """The pre-existing compensation path is unchanged."""
        vector_store = _FakeVectorStore()
        manager = CollectionPerKbManager(
            _HangingUpsertStorage(failure=RuntimeError("boom")), vector_store
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await manager.create_knowledge_base(
                "user-1",
                "kb",
                "",
                _embedding_config(),
            )

        self.assertEqual(vector_store.deleted, vector_store.created)

    async def test_second_cancel_during_delete_cannot_leak(self) -> None:
        """A cancellation arriving inside the compensating delete is held off.

        The delete runs under ``asyncio.shield``, so a second ``cancel()``
        while it is in flight must not cut it short — the collection is
        still dropped and the cancellation still surfaces.
        """
        vector_store = _FakeVectorStore()
        vector_store.delete_release = asyncio.Event()
        manager = CollectionPerKbManager(_HangingUpsertStorage(), vector_store)

        task = asyncio.create_task(
            manager.create_knowledge_base(
                "user-1",
                "kb",
                "",
                _embedding_config(),
            )
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        task.cancel()
        # wait for the compensating delete to start, then cancel again
        await asyncio.wait_for(vector_store.delete_started.wait(), timeout=5)
        task.cancel()
        vector_store.delete_release.set()

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=5)

        self.assertEqual(vector_store.deleted, vector_store.created)


if __name__ == "__main__":
    unittest.main()
