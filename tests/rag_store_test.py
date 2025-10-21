# -*- coding: utf-8 -*-
"""Test the RAG store implementations."""
import os
from unittest import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.rag import (
    QdrantStore,
    Document,
    DocMetadata,
    MilvusLiteStore,
)


class RAGStoreTest(IsolatedAsyncioTestCase):
    """Test cases for RAG store implementations."""

    async def asyncSetUp(self) -> None:
        """Set up before each test."""
        # Remove the test database file after the test
        if os.path.exists("./milvus_demo.db"):
            os.remove("./milvus_demo.db")

    async def test_qdrant_store(self) -> None:
        """Test the QdrantStore implementation."""
        store = QdrantStore(
            location=":memory:",
            collection_name="test",
            dimensions=3,
        )

        await store.add(
            [
                Document(
                    embedding=[0.1, 0.2, 0.3],
                    metadata=DocMetadata(
                        content=TextBlock(
                            type="text",
                            text="This is a test document.",
                        ),
                        doc_id="doc1",
                        chunk_id=0,
                        total_chunks=2,
                    ),
                ),
                Document(
                    embedding=[0.9, 0.1, 0.4],
                    metadata=DocMetadata(
                        content=TextBlock(
                            type="text",
                            text="This is another test document.",
                        ),
                        doc_id="doc1",
                        chunk_id=1,
                        total_chunks=2,
                    ),
                ),
            ],
        )

        res = await store.search(
            query_embedding=[0.15, 0.25, 0.35],
            limit=3,
            score_threshold=0.8,
        )
        self.assertEqual(len(res), 1)
        self.assertEqual(
            res[0].score,
            0.9974149072579597,
        )
        self.assertEqual(
            res[0].metadata.content["text"],
            "This is a test document.",
        )

    async def test_milvus_lite_store(self) -> None:
        """Test the MilvusLiteStore implementation."""
        if os.name == "nt":
            self.skipTest("Milvus Lite is not supported on Windows.")

        store = MilvusLiteStore(
            uri="./milvus_demo.db",
            collection_name="test_milvus",
            dimensions=3,
        )

        await store.add(
            [
                Document(
                    embedding=[0.1, 0.2, 0.3],
                    metadata=DocMetadata(
                        content=TextBlock(
                            type="text",
                            text="This is a test document.",
                        ),
                        doc_id="doc1",
                        chunk_id=0,
                        total_chunks=2,
                    ),
                ),
                Document(
                    embedding=[0.9, 0.1, 0.4],
                    metadata=DocMetadata(
                        content=TextBlock(
                            type="text",
                            text="This is another test document.",
                        ),
                        doc_id="doc1",
                        chunk_id=1,
                        total_chunks=2,
                    ),
                ),
            ],
        )

        res = await store.search(
            query_embedding=[0.15, 0.25, 0.35],
            limit=3,
            score_threshold=0.8,
        )
        self.assertEqual(len(res), 1)
        self.assertEqual(
            round(res[0].score, 4),
            0.9974,
        )
        self.assertEqual(
            res[0].metadata.content["text"],
            "This is a test document.",
        )

    async def asyncTearDown(self) -> None:
        """Clean up after tests."""
        # Remove Milvus Lite database file
        if os.path.exists("./milvus_demo.db"):
            os.remove("./milvus_demo.db")
