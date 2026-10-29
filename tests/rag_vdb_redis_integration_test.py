# -*- coding: utf-8 -*-
"""Integration tests for RedisStore against a real Redis instance.

Skipped by default — set ``REDIS_URL`` to run against a live Redis
Stack 7.2+ or Redis 8.0+ (requires the RediSearch module).

.. code-block:: bash

    REDIS_URL=redis://localhost:6380 uv run pytest tests/rag_vdb_redis_integration_test.py -v
"""  # noqa: E501

import os
import uuid
from contextlib import AsyncExitStack
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.rag import Chunk, RedisStore, VectorRecord

_REDIS_URL = os.environ.get("REDIS_URL")


class RedisStoreIntegrationTest(IsolatedAsyncioTestCase):
    """End-to-end verification against a real Redis with RediSearch."""

    async def asyncSetUp(self) -> None:
        if not _REDIS_URL:
            self.skipTest("REDIS_URL not set")
        self._exit_stack = AsyncExitStack()
        self.store = await self._exit_stack.enter_async_context(
            RedisStore(url=_REDIS_URL),
        )
        self._collection = f"agentscope_it_{uuid.uuid4().hex}"

    async def asyncTearDown(self) -> None:
        if not _REDIS_URL:
            return
        await self.store.delete_collection(self._collection)
        await self._exit_stack.aclose()

    async def test_special_chars_document_id(self) -> None:
        """document_id with special characters round-trips correctly."""
        await self.store.create_collection(self._collection, dimensions=3)
        doc_id = r"doc,$|{}\\"

        await self.store.insert(
            self._collection,
            [
                VectorRecord(
                    vector=[1.0, 0.0, 0.0],
                    document_id=doc_id,
                    chunk=Chunk(
                        content=TextBlock(text="test"),
                        source="test.txt",
                        chunk_index=0,
                        total_chunks=1,
                    ),
                ),
            ],
        )

        hits = await self.store.search(
            self._collection,
            [1.0, 0.0, 0.0],
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].document_id, doc_id)

    async def test_metadata_filter_with_special_chars(self) -> None:
        """metadata_filter works with keys and values containing
        special characters."""
        await self.store.create_collection(self._collection, dimensions=3)

        await self.store.insert(
            self._collection,
            [
                VectorRecord(
                    vector=[1.0, 0.0, 0.0],
                    document_id="doc-1",
                    chunk=Chunk(
                        content=TextBlock(text="a"),
                        source="a.txt",
                        chunk_index=0,
                        total_chunks=1,
                        metadata={"tenant.id": r"team,$1"},
                    ),
                ),
                VectorRecord(
                    vector=[0.0, 1.0, 0.0],
                    document_id="doc-2",
                    chunk=Chunk(
                        content=TextBlock(text="b"),
                        source="b.txt",
                        chunk_index=0,
                        total_chunks=1,
                        metadata={"tenant.id": "team-b"},
                    ),
                ),
            ],
        )

        # Filter should only match doc-1
        hits = await self.store.search(
            self._collection,
            [1.0, 0.0, 0.0],
            metadata_filter={"tenant.id": r"team,$1"},
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].document_id, "doc-1")

    async def test_list_and_delete(self) -> None:
        """list_documents + delete + re-list works end-to-end."""
        await self.store.create_collection(self._collection, dimensions=3)
        doc_id = "doc-1"

        await self.store.insert(
            self._collection,
            [
                VectorRecord(
                    vector=[1.0, 0.0, 0.0],
                    document_id=doc_id,
                    chunk=Chunk(
                        content=TextBlock(text="hello"),
                        source="hello.txt",
                        chunk_index=0,
                        total_chunks=1,
                    ),
                ),
            ],
        )

        documents = await self.store.list_documents(self._collection)
        self.assertEqual([d.document_id for d in documents], [doc_id])

        await self.store.delete(self._collection, doc_id)
        self.assertEqual(
            await self.store.list_documents(self._collection),
            [],
        )

    async def test_collection_lifecycle(self) -> None:
        """create / has / delete collection against real Redis."""
        await self.store.create_collection(self._collection, dimensions=3)
        self.assertTrue(await self.store.has_collection(self._collection))

        # Re-creating is a no-op
        await self.store.create_collection(self._collection, dimensions=3)
        self.assertTrue(await self.store.has_collection(self._collection))

        await self.store.delete_collection(self._collection)
        self.assertFalse(await self.store.has_collection(self._collection))
