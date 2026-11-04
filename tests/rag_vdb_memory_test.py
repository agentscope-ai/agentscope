# -*- coding: utf-8 -*-
"""Tests for :class:`MemoryVectorStore`."""

from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.rag._document import Chunk
from agentscope.rag._vdb import MemoryVectorStore, VectorRecord


class MemoryVectorStoreTest(IsolatedAsyncioTestCase):
    """Test the in-memory vector store."""

    async def asyncSetUp(self) -> None:
        """Set up the store for tests."""
        self.store = MemoryVectorStore()
        await self.store.create_collection("test_col", dimensions=3)

    async def test_insert_and_search(self) -> None:
        """It can insert records and search them by cosine similarity."""
        records = [
            VectorRecord(
                vector=[1.0, 0.0, 0.0],
                document_id="doc1",
                chunk=Chunk(
                    content=TextBlock(text="x-axis"),
                    chunk_index=0,
                    total_chunks=1,
                    source="doc1",
                ),
            ),
            VectorRecord(
                vector=[0.0, 1.0, 0.0],
                document_id="doc2",
                chunk=Chunk(
                    content=TextBlock(text="y-axis"),
                    chunk_index=0,
                    total_chunks=1,
                    source="doc2",
                ),
            ),
        ]

        await self.store.insert("test_col", records)

        # Search exact match for x-axis
        results = await self.store.search("test_col", [1.0, 0.0, 0.0], top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, "doc1")
        self.assertAlmostEqual(results[0].score, 1.0)

        # Search closer to y-axis
        results = await self.store.search("test_col", [0.1, 0.9, 0.0], top_k=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].document_id, "doc2")
        self.assertEqual(results[1].document_id, "doc1")

    async def test_delete(self) -> None:
        """It deletes all records for a given document id."""
        records = [
            VectorRecord(
                vector=[1.0, 0.0, 0.0],
                document_id="doc1",
                chunk=Chunk(
                    content=TextBlock(text="p1"),
                    chunk_index=0,
                    total_chunks=1,
                    source="doc1",
                ),
            ),
            VectorRecord(
                vector=[0.0, 1.0, 0.0],
                document_id="doc2",
                chunk=Chunk(
                    content=TextBlock(text="p2"),
                    chunk_index=0,
                    total_chunks=1,
                    source="doc2",
                ),
            ),
        ]
        await self.store.insert("test_col", records)
        await self.store.delete("test_col", "doc1")

        # Search should only find doc2
        results = await self.store.search(
            "test_col",
            [1.0, 1.0, 1.0],
            top_k=10,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, "doc2")

    async def test_list_documents(self) -> None:
        """It lists unique documents."""
        records = [
            VectorRecord(
                vector=[1.0, 0.0, 0.0],
                document_id="doc1",
                chunk=Chunk(
                    content=TextBlock(text="p1"),
                    chunk_index=0,
                    total_chunks=2,
                    source="doc1.txt",
                ),
            ),
            VectorRecord(
                vector=[0.5, 0.5, 0.0],
                document_id="doc1",
                chunk=Chunk(
                    content=TextBlock(text="p2"),
                    chunk_index=1,
                    total_chunks=2,
                    source="doc1.txt",
                ),
            ),
        ]
        await self.store.insert("test_col", records)

        docs = await self.store.list_documents("test_col")
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].document_id, "doc1")
        self.assertEqual(docs[0].chunk_count, 2)
        self.assertEqual(docs[0].source, "doc1.txt")
