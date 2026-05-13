# -*- coding: utf-8 -*-
"""Test the RAG store implementations."""
import os
import types
import uuid
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch, AsyncMock

from agentscope.message import TextBlock
from agentscope.rag import (
    QdrantStore,
    Document,
    DocMetadata,
    MilvusLiteStore,
    OceanBaseStore,
    AlibabaCloudMySQLStore,
    MongoDBStore,
    PgVectorStore,
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
            res[0].score // 0.0001,
            9974,
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

    async def test_oceanbase_store(self) -> None:
        """Use real OceanBase when env is provided, otherwise use a mock."""

        def _make_mock_pyobvector(
            search_rows: list[dict],
        ) -> tuple[types.SimpleNamespace, MagicMock]:
            """Create a minimal pyobvector mock aligned with the
            existing style."""
            mock_client = MagicMock()
            mock_client.has_collection.return_value = False
            mock_client.create_schema.return_value = MagicMock()
            mock_client.prepare_index_params.return_value = MagicMock()
            mock_client.search.return_value = search_rows

            mock_pyobvector = types.SimpleNamespace(
                MilvusLikeClient=MagicMock(return_value=mock_client),
                DataType=types.SimpleNamespace(
                    VARCHAR="VARCHAR",
                    FLOAT_VECTOR="FLOAT_VECTOR",
                    STRING="STRING",
                    INT64="INT64",
                    JSON="JSON",
                ),
            )
            return mock_pyobvector, mock_client

        required_vars = [
            "OCEANBASE_URI",
            "OCEANBASE_USER",
            "OCEANBASE_DB",
        ]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            # COSINE distance = 1 - similarity
            mock_rows = [
                {
                    "doc_id": "doc1",
                    "chunk_id": 0,
                    "total_chunks": 2,
                    "content": {
                        "type": "text",
                        "text": "This is a test document.",
                    },
                    "distance": 1.0 - 0.9974,
                },
            ]
            mock_pyobvector, mock_client = _make_mock_pyobvector(mock_rows)

            with patch.dict("sys.modules", {"pyobvector": mock_pyobvector}):
                store = OceanBaseStore(
                    collection_name=f"test_ob_{uuid.uuid4().hex[:8]}",
                    dimensions=3,
                    uri="127.0.0.1:2881",
                    user="root@test",
                    password="",
                    db_name="test",
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
                )

                self.assertTrue(mock_client.insert.called)
                self.assertTrue(mock_client.create_collection.called)

                res = await store.search(
                    query_embedding=[0.15, 0.25, 0.35],
                    limit=3,
                    score_threshold=0.8,
                )
                self.assertEqual(len(res), 1)
                self.assertEqual(round(res[0].score, 4), 0.9974)
                self.assertEqual(
                    res[0].metadata.content["text"],
                    "This is a test document.",
                )

                await store.delete(ids=["dummy-id"])
                self.assertTrue(mock_client.delete.called)
            return

        collection_name = f"test_ob_{uuid.uuid4().hex[:8]}"
        store = OceanBaseStore(
            collection_name=collection_name,
            dimensions=3,
            uri=os.getenv("OCEANBASE_URI", ""),
            user=os.getenv("OCEANBASE_USER", ""),
            password=os.getenv("OCEANBASE_PASSWORD", ""),
            db_name=os.getenv("OCEANBASE_DB", ""),
        )

        client = store.get_client()
        client.drop_collection(collection_name)

        try:
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
        finally:
            client.drop_collection(collection_name)

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

    async def test_mongodb_store(self) -> None:
        """Test the MongoDBStore implementation using mocks."""
        # Create mock pymongo module
        mock_pymongo = MagicMock()
        mock_operations = MagicMock()
        mock_pymongo.operations = mock_operations

        # Create mock AsyncMongoClient
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_collection = MagicMock()

        # Configure mock client
        mock_pymongo.AsyncMongoClient.return_value = mock_client
        mock_client.get_database.return_value = mock_db

        # Configure mock database
        mock_db.list_collection_names = AsyncMock(return_value=[])
        mock_db.create_collection = AsyncMock(return_value=mock_collection)
        mock_db.get_collection.return_value = mock_collection

        # Configure mock collection
        mock_collection.create_search_index = AsyncMock()
        mock_collection.bulk_write = AsyncMock()
        mock_collection.delete_many = AsyncMock()
        mock_collection.drop = AsyncMock()

        # Mock list_search_indexes to return queryable index
        async def mock_index_iter() -> AsyncGenerator:
            yield {"queryable": True}

        mock_collection.list_search_indexes = AsyncMock(
            return_value=mock_index_iter(),
        )

        # Mock aggregate to return search results
        mock_search_results = [
            {
                "vector": [0.1, 0.2, 0.3],
                "payload": {
                    "doc_id": "doc1",
                    "chunk_id": 0,
                    "total_chunks": 2,
                    "content": {
                        "type": "text",
                        "text": "This is a test document.",
                    },
                },
                "score": 0.97,
            },
        ]

        async def mock_aggregate_iter() -> AsyncGenerator:
            for item in mock_search_results:
                yield item

        mock_collection.aggregate = AsyncMock(
            return_value=mock_aggregate_iter(),
        )

        # Mock client close
        mock_client.close = AsyncMock()
        mock_client.drop_database = AsyncMock()

        # Mock ReplaceOne
        mock_replace_one = MagicMock()
        mock_pymongo.ReplaceOne = mock_replace_one

        with patch.dict(
            "sys.modules",
            {
                "pymongo": mock_pymongo,
                "pymongo.operations": mock_operations,
            },
        ):
            # Create store instance
            store = MongoDBStore(
                host="mongodb://localhost:27017",
                db_name="test_db",
                collection_name="test_collection",
                dimensions=3,
                distance="cosine",
            )

            # Verify client was created
            mock_pymongo.AsyncMongoClient.assert_called_once()

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

            # Verify add operations
            self.assertTrue(mock_collection.bulk_write.called)

            # Reset mocks for search operation
            mock_collection.reset_mock()

            # Reconfigure list_search_indexes for search
            mock_collection.list_search_indexes = AsyncMock(
                return_value=mock_index_iter(),
            )
            mock_collection.aggregate = AsyncMock(
                return_value=mock_aggregate_iter(),
            )

            # Test search operation
            res = await store.search(
                query_embedding=[0.15, 0.25, 0.35],
                limit=3,
                score_threshold=0.8,
            )

            # Verify search results
            self.assertEqual(len(res), 1)
            self.assertAlmostEqual(res[0].score, 0.97, places=2)
            self.assertEqual(
                res[0].metadata.content["text"],
                "This is a test document.",
            )
            self.assertEqual(res[0].metadata.doc_id, "doc1")
            self.assertEqual(res[0].metadata.chunk_id, 0)
            self.assertEqual(res[0].metadata.total_chunks, 2)

            # Verify search executed aggregate
            self.assertTrue(mock_collection.aggregate.called)

            # Test delete operation
            await store.delete(ids="doc1")

            # Verify delete executed
            mock_collection.delete_many.assert_called_with(
                {"payload.doc_id": {"$in": ["doc1"]}},
            )

            # Test delete_collection
            await store.delete_collection()
            mock_collection.drop.assert_called()

            # Test delete_database
            await store.delete_database()
            mock_client.drop_database.assert_called_with("test_db")

            # Test close
            await store.close()
            mock_client.close.assert_called()

    async def test_pgvector_store(self) -> None:
        """Test the PgVectorStore implementation using mocks."""
        # Create mock psycopg2 module
        mock_psycopg2 = MagicMock()

        # Create mock cursor and connection
        mock_cursor = MagicMock()
        mock_conn = MagicMock()

        # Configure mock connection to return mock cursor
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        # Mock the search query result
        # Simulate a database row returned by fetchall
        mock_search_result = [
            (
                "test-uuid-1",  # id
                "doc1",  # doc_id
                0,  # chunk_id
                '{"type": "text", "text": "This is a test document."}',
                2,  # total_chunks
                0.03,  # distance (low distance = high similarity for COSINE)
            ),
        ]

        # Use patch.dict to mock sys.modules
        with patch.dict(
            "sys.modules",
            {
                "psycopg2": mock_psycopg2,
            },
        ):
            # Create store instance
            store = PgVectorStore(
                host="test-host",
                port=5432,
                user="test-user",
                password="test-password",
                database="test-database",
                table_name="test_vectors",
                dimensions=3,
                distance="COSINE",
            )

            # Verify connection was established
            mock_psycopg2.connect.assert_called_once()

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

            # Verify add operations executed SQL
            self.assertTrue(mock_cursor.execute.called)

            # Reset mock for search operation
            mock_cursor.reset_mock()

            # Configure mock to return search results
            mock_cursor.fetchall.return_value = mock_search_result

            # Test search operation
            res = await store.search(
                query_embedding=[0.15, 0.25, 0.35],
                limit=3,
                score_threshold=0.1,  # Distance threshold
            )

            # Verify search results
            self.assertEqual(len(res), 1)
            # For pgvector COSINE, score is the distance value itself
            self.assertAlmostEqual(res[0].score, 0.03, places=2)
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

            # Test search with filter
            mock_cursor.reset_mock()
            mock_cursor.fetchall.return_value = mock_search_result

            res = await store.search(
                query_embedding=[0.15, 0.25, 0.35],
                limit=3,
                filter="doc_id = 'doc1'",
            )

            # Verify filter was applied (check that execute was called)
            self.assertTrue(mock_cursor.execute.called)
            self.assertEqual(len(res), 1)

            # Test delete operation
            await store.delete(ids=["test-uuid-1"])

            # Verify delete executed SQL
            self.assertTrue(mock_cursor.execute.called)

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
