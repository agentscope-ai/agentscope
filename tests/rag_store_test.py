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
    AlibabaCloudMySQLStore,
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

    async def test_alibabacloud_mysql_store(self) -> None:
        """Test the AlibabaCloudMySQLStore implementation using mocks."""
        # Create mock MySQL module and connector
        mock_mysql_connector = MagicMock()
        mock_mysql = MagicMock()
        mock_mysql.connector = mock_mysql_connector

        # Create mock cursor and connection
        mock_cursor = MagicMock()
        mock_conn = MagicMock()

        # Configure mock connection to return mock cursor
        mock_conn.cursor.return_value = mock_cursor
        mock_mysql_connector.connect.return_value = mock_conn

        # Mock the search query result
        # Simulate a database row returned by fetchall
        mock_search_result = [
            {
                "id": "test-uuid-1",
                "doc_id": "doc1",
                "chunk_id": 0,
                "content": (
                    '{"type": "text", "text": "This is a test document."}'
                ),
                "total_chunks": 2,
                "distance": 0.03,  # Low distance = high similarity
            },
        ]

        # Use patch.dict to mock sys.modules
        with patch.dict(
            "sys.modules",
            {
                "mysql": mock_mysql,
                "mysql.connector": mock_mysql_connector,
            },
        ):
            # Create store instance
            store = AlibabaCloudMySQLStore(
                host="test-host",
                port=3306,
                user="test-user",
                password="test-password",
                database="test-database",
                table_name="test_vectors",
                dimensions=3,
            )

            # Verify connection was established
            mock_mysql_connector.connect.assert_called_once()

            # Test add operation
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
                        embedding=[2.0, 3.8, 2.7],
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

            # Verify add operations executed SQL
            self.assertTrue(mock_cursor.execute.called)
            self.assertTrue(mock_conn.commit.called)

            # Reset mock for search operation
            mock_cursor.reset_mock()
            mock_conn.reset_mock()

            # Configure mock to return search results
            mock_cursor.fetchall.return_value = mock_search_result

            # Test search operation
            res = await store.search(
                query_embedding=[0.15, 0.25, 0.35],
                limit=3,
                score_threshold=0.95,
            )

            # Verify search results
            self.assertEqual(len(res), 1)
            # Score = 1 - distance = 1 - 0.03 = 0.97
            self.assertAlmostEqual(res[0].score, 0.97, places=2)
            self.assertEqual(
                res[0].metadata.content["text"],
                "This is a test document.",
            )
            self.assertEqual(res[0].metadata.doc_id, "doc1")
            self.assertEqual(res[0].metadata.chunk_id, 0)
            self.assertEqual(res[0].metadata.total_chunks, 2)

            # Verify search executed SQL query
            self.assertTrue(mock_cursor.execute.called)
            self.assertTrue(mock_cursor.fetchall.called)

            # Test delete operation
            await store.delete(filter='doc_id = "doc1"')

            # Verify delete executed SQL
            self.assertTrue(mock_conn.commit.called)

            # Test close
            store.close()

            # Verify connections were closed
            mock_cursor.close.assert_called()
            mock_conn.close.assert_called()

    async def asyncTearDown(self) -> None:
        """Clean up after tests."""
        # Remove Milvus Lite database file
        if os.path.exists("./milvus_demo.db"):
            os.remove("./milvus_demo.db")
