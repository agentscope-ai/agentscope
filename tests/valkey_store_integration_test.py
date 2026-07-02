# -*- coding: utf-8 -*-
"""Integration tests for the ValkeyStore class.

These tests require a running Valkey server with the Search module
on localhost:6379. They are gated behind the environment variable
``VALKEY_INTEGRATION_TEST=true``.

Run with:
    VALKEY_INTEGRATION_TEST=true uv run python -m pytest \
        tests/valkey_store_integration_test.py -xvs
"""

import os
import unittest
from contextlib import AsyncExitStack
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    ValkeyStore,
    VectorRecord,
)

SKIP_REASON = (
    "Valkey integration tests require VALKEY_INTEGRATION_TEST=true "
    "and a running Valkey server with the Search module on localhost:6379"
)


def _should_skip() -> bool:
    return os.environ.get("VALKEY_INTEGRATION_TEST", "").lower() != "true"


def _make_record(
    text: str,
    vector: list[float],
    document_id: str,
    chunk_index: int = 0,
    total_chunks: int = 1,
    metadata: dict | None = None,
) -> VectorRecord:
    """Build a VectorRecord for testing."""
    return VectorRecord(
        vector=vector,
        document_id=document_id,
        chunk=Chunk(
            content=TextBlock(text=text),
            source=f"{document_id}.txt",
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            metadata=metadata or {},
        ),
    )


@unittest.skipIf(_should_skip(), SKIP_REASON)
class ValkeyStoreIntegrationTest(IsolatedAsyncioTestCase):
    """Integration tests for ValkeyStore against a real Valkey server."""

    COLLECTION = "test_agentscope_integration"

    async def asyncSetUp(self) -> None:
        """Connect to Valkey and clean up any leftover test index."""
        self._exit_stack = AsyncExitStack()
        self.store = await self._exit_stack.enter_async_context(
            ValkeyStore(
                host=os.environ.get("VALKEY_HOST", "localhost"),
                port=int(os.environ.get("VALKEY_PORT", "6379")),
            ),
        )
        # Clean up from any previous failed run
        await self.store.delete_collection(self.COLLECTION)

    async def asyncTearDown(self) -> None:
        """Drop the test collection and close the connection."""
        try:
            await self.store.delete_collection(self.COLLECTION)
        except Exception:
            pass
        await self._exit_stack.aclose()

    async def test_collection_lifecycle(self) -> None:
        """Collections can be created, checked, and deleted."""
        self.assertFalse(await self.store.has_collection(self.COLLECTION))

        await self.store.create_collection(self.COLLECTION, dimensions=3)
        self.assertTrue(await self.store.has_collection(self.COLLECTION))

        # Creating an existing collection is a no-op
        await self.store.create_collection(self.COLLECTION, dimensions=3)
        self.assertTrue(await self.store.has_collection(self.COLLECTION))

        await self.store.delete_collection(self.COLLECTION)
        self.assertFalse(await self.store.has_collection(self.COLLECTION))

    async def test_insert_and_search(self) -> None:
        """Inserted records are searchable, ordered by similarity."""
        await self.store.create_collection(self.COLLECTION, dimensions=3)

        await self.store.insert(
            self.COLLECTION,
            [
                _make_record(
                    "Hello world!",
                    [1.0, 0.0, 0.0],
                    document_id="doc-1",
                    chunk_index=0,
                    total_chunks=2,
                ),
                _make_record(
                    "Goodbye world!",
                    [0.0, 1.0, 0.0],
                    document_id="doc-1",
                    chunk_index=1,
                    total_chunks=2,
                ),
                _make_record(
                    "Orthogonal",
                    [0.0, 0.0, 1.0],
                    document_id="doc-2",
                ),
            ],
        )

        # Search for vectors similar to [1, 0, 0]
        results = await self.store.search(
            self.COLLECTION,
            query_vector=[1.0, 0.0, 0.0],
            top_k=3,
        )

        self.assertEqual(len(results), 3)
        # Most similar should be "Hello world!" (exact match)
        self.assertEqual(results[0].chunk.content.text, "Hello world!")
        self.assertAlmostEqual(results[0].score, 1.0, places=4)
        self.assertEqual(results[0].document_id, "doc-1")

    async def test_search_top_k(self) -> None:
        """top_k limits the number of returned results."""
        await self.store.create_collection(self.COLLECTION, dimensions=3)

        await self.store.insert(
            self.COLLECTION,
            [
                _make_record("A", [1.0, 0.0, 0.0], document_id="doc-1"),
                _make_record("B", [0.9, 0.1, 0.0], document_id="doc-2"),
                _make_record("C", [0.0, 0.0, 1.0], document_id="doc-3"),
            ],
        )

        results = await self.store.search(
            self.COLLECTION,
            query_vector=[1.0, 0.0, 0.0],
            top_k=1,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk.content.text, "A")

    async def test_delete_by_document_id(self) -> None:
        """delete removes all records of one document only."""
        await self.store.create_collection(self.COLLECTION, dimensions=3)

        await self.store.insert(
            self.COLLECTION,
            [
                _make_record(
                    "doc1-chunk0",
                    [1.0, 0.0, 0.0],
                    document_id="doc-1",
                    chunk_index=0,
                    total_chunks=2,
                ),
                _make_record(
                    "doc1-chunk1",
                    [0.9, 0.1, 0.0],
                    document_id="doc-1",
                    chunk_index=1,
                    total_chunks=2,
                ),
                _make_record(
                    "doc2-chunk0",
                    [0.0, 1.0, 0.0],
                    document_id="doc-2",
                ),
            ],
        )

        await self.store.delete(self.COLLECTION, document_id="doc-1")

        results = await self.store.search(
            self.COLLECTION,
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
        )

        # Only doc-2 should remain
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, "doc-2")
        self.assertEqual(results[0].chunk.content.text, "doc2-chunk0")

    async def test_insert_empty_records(self) -> None:
        """Inserting an empty record list is a no-op."""
        await self.store.create_collection(self.COLLECTION, dimensions=3)
        await self.store.insert(self.COLLECTION, [])

        results = await self.store.search(
            self.COLLECTION,
            query_vector=[1.0, 0.0, 0.0],
        )
        self.assertEqual(results, [])

    async def test_list_documents(self) -> None:
        """list_documents groups chunks by document_id."""
        await self.store.create_collection(self.COLLECTION, dimensions=3)

        await self.store.insert(
            self.COLLECTION,
            [
                VectorRecord(
                    vector=[1.0, 0.0, 0.0],
                    document_id="doc-1",
                    chunk=Chunk(
                        content=TextBlock(text="A"),
                        source="alpha.txt",
                        chunk_index=0,
                        total_chunks=2,
                        metadata={"media_type": "text/plain"},
                    ),
                ),
                VectorRecord(
                    vector=[0.9, 0.1, 0.0],
                    document_id="doc-1",
                    chunk=Chunk(
                        content=TextBlock(text="B"),
                        source="alpha.txt",
                        chunk_index=1,
                        total_chunks=2,
                        metadata={"media_type": "text/plain"},
                    ),
                ),
                VectorRecord(
                    vector=[0.0, 1.0, 0.0],
                    document_id="doc-2",
                    chunk=Chunk(
                        content=TextBlock(text="C"),
                        source="beta.md",
                        chunk_index=0,
                        total_chunks=1,
                        metadata={"media_type": "text/markdown"},
                    ),
                ),
            ],
        )

        summaries = await self.store.list_documents(self.COLLECTION)
        summaries_by_id = {s.document_id: s for s in summaries}

        self.assertEqual(set(summaries_by_id), {"doc-1", "doc-2"})
        self.assertEqual(summaries_by_id["doc-1"].chunk_count, 2)
        self.assertEqual(summaries_by_id["doc-1"].source, "alpha.txt")
        self.assertEqual(
            summaries_by_id["doc-1"].metadata,
            {"media_type": "text/plain"},
        )
        self.assertEqual(summaries_by_id["doc-2"].chunk_count, 1)
        self.assertEqual(summaries_by_id["doc-2"].source, "beta.md")

    async def test_search_with_document_id_filter(self) -> None:
        """search respects metadata_filter on document_id."""
        await self.store.create_collection(self.COLLECTION, dimensions=3)

        await self.store.insert(
            self.COLLECTION,
            [
                _make_record("A", [1.0, 0.0, 0.0], document_id="doc-1"),
                _make_record("B", [0.9, 0.1, 0.0], document_id="doc-2"),
            ],
        )

        results = await self.store.search(
            self.COLLECTION,
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"document_id": "doc-2"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, "doc-2")
