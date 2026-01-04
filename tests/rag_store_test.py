# -*- coding: utf-8 -*-
"""Test the RAG store implementations."""
import os
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from agentscope.message import TextBlock
from agentscope.rag import (
    QdrantStore,
    Document,
    DocMetadata,
    MilvusLiteStore,
    LindormStore,
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
        if os.path.exists("./milvus_demo.db"):
            os.remove("./milvus_demo.db")

    @patch("opensearchpy.OpenSearch")
    async def test_lindorm_store(
        self,
        mock_opensearch_class: MagicMock,
    ) -> None:
        """Test the LindormStore implementation."""
        mock_client = MagicMock()
        mock_opensearch_class.return_value = mock_client

        mock_client.indices.exists.return_value = False
        mock_client.indices.create.return_value = {"acknowledged": True}
        mock_client.index.return_value = {"result": "created"}
        mock_client.indices.refresh.return_value = {
            "_shards": {"successful": 1}
        }
        mock_client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_score": 0.95,
                        "_source": {
                            "vector": [0.1, 0.2, 0.3],
                            "doc_id": "doc1",
                            "chunk_id": 0,
                            "content": "This is a test document.",
                            "total_chunks": 2,
                        },
                    },
                ],
            },
        }

        store = LindormStore(
            hosts=["http://localhost:9200"],
            index_name="test_index",
            dimensions=3,
            http_auth=("user", "pass"),
            enable_routing=True,
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
            ],
            routing="user123",
        )

        mock_client.indices.create.assert_called_once()
        self.assertTrue(mock_client.index.called)

        res = await store.search(
            query_embedding=[0.15, 0.25, 0.35],
            limit=3,
            score_threshold=0.9,
            routing="user123",
        )

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].score, 0.95)
        self.assertEqual(
            res[0].metadata.content,
            "This is a test document.",
        )

        call_args = mock_client.search.call_args
        query_body = call_args[1]["body"]
        self.assertEqual(query_body["size"], 3)
        self.assertIn("knn", query_body["query"])
