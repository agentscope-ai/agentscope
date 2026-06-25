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
    ValkeyStore,
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

    async def asyncTearDown(self) -> None:
        """Clean up after tests."""
        # Remove Milvus Lite database file
        if os.path.exists("./milvus_demo.db"):
            os.remove("./milvus_demo.db")

    async def test_valkey_store(self) -> None:  # pylint: disable=R0915
        """Test the ValkeyStore implementation using mocks."""
        import json

        # Create mock glide modules
        mock_glide = MagicMock()
        mock_glide_shared = MagicMock()
        mock_ft_create_options = MagicMock()
        mock_ft_search_options = MagicMock()

        # Mock GlideClient and GlideClusterClient
        mock_client_instance = AsyncMock()
        mock_glide.GlideClient = MagicMock()
        mock_glide.GlideClient.create = AsyncMock(
            return_value=mock_client_instance,
        )
        mock_glide.GlideClusterClient = MagicMock()
        mock_glide.GlideClientConfiguration = MagicMock()
        mock_glide.GlideClusterClientConfiguration = MagicMock()
        mock_glide.NodeAddress = MagicMock()

        # Mock Batch
        mock_batch_instance = MagicMock()
        mock_batch_instance.hset = MagicMock()
        mock_glide.Batch = MagicMock(return_value=mock_batch_instance)

        # Mock ft module
        mock_ft = MagicMock()
        mock_ft.list = AsyncMock(return_value=[])
        mock_ft.create = AsyncMock(return_value="OK")
        mock_ft.dropindex = AsyncMock(return_value="OK")
        mock_glide.ft = mock_ft

        # Mock ft_create_options classes
        mock_ft_create_options.FtCreateOptions = MagicMock()
        mock_ft_create_options.DataType = MagicMock()
        mock_ft_create_options.DataType.HASH = "HASH"
        mock_ft_create_options.VectorField = MagicMock()
        mock_ft_create_options.VectorAlgorithm = MagicMock()
        mock_ft_create_options.VectorAlgorithm.HNSW = "HNSW"
        mock_ft_create_options.VectorFieldAttributesHnsw = MagicMock()
        mock_ft_create_options.DistanceMetricType = MagicMock()
        mock_ft_create_options.DistanceMetricType.COSINE = "COSINE"
        mock_ft_create_options.VectorType = MagicMock()
        mock_ft_create_options.VectorType.FLOAT32 = "FLOAT32"
        mock_ft_create_options.TagField = MagicMock()
        mock_ft_create_options.NumericField = MagicMock()

        # Mock ft_search_options classes
        mock_ft_search_options.FtSearchOptions = MagicMock()
        mock_ft_search_options.FtSearchLimit = MagicMock()
        mock_ft_search_options.ReturnField = MagicMock()

        # Mock hset
        mock_client_instance.hset = AsyncMock()

        # Mock exec (for batch operations)
        mock_client_instance.exec = AsyncMock(return_value=[])

        # Mock delete
        mock_client_instance.delete = AsyncMock()

        # Prepare search response
        # Valkey FT.SEARCH returns [count, {key: {field: value}}]
        metadata_doc = {
            "doc_id": "doc1",
            "chunk_id": 0,
            "total_chunks": 2,
            "content": {
                "type": "text",
                "text": "This is a test document.",
            },
        }
        # COSINE distance of 0.0026 => similarity = 1 - 0.0026 = 0.9974
        mock_search_response = [
            1,
            {
                b"agentscope:doc:some-uuid": {
                    b"metadata": json.dumps(metadata_doc).encode(),
                    b"vector_score": b"0.0026",
                },
            },
        ]
        mock_ft.search = AsyncMock(return_value=mock_search_response)

        # Mock search for delete (find keys by doc_id)
        mock_delete_search_response = [
            2,
            {
                b"agentscope:doc:uuid-1": {},
                b"agentscope:doc:uuid-2": {},
            },
        ]

        with patch.dict(
            "sys.modules",
            {
                "glide": mock_glide,
                "glide.async_commands": MagicMock(),
                "glide.async_commands.ft": mock_ft,
                "glide_shared": mock_glide_shared,
                "glide_shared.commands": MagicMock(),
                "glide_shared.commands.server_modules": MagicMock(),
                "glide_shared.commands.server_modules.ft_options": (
                    MagicMock()
                ),
                "glide_shared.commands.server_modules.ft_options"
                ".ft_create_options": mock_ft_create_options,
                "glide_shared.commands.server_modules.ft_options"
                ".ft_search_options": mock_ft_search_options,
            },
        ):
            store = ValkeyStore(
                host="localhost",
                port=6379,
                index_name="test_idx",
                prefix="test:doc:",
                dimensions=3,
                distance="COSINE",
            )

            # Inject the mock client directly
            store._client = mock_client_instance  # pylint: disable=W0212

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

            # Verify exec was called (batch HSET)
            self.assertTrue(mock_client_instance.exec.called)

            # Test search operation
            res = await store.search(
                query_embedding=[0.15, 0.25, 0.35],
                limit=3,
                score_threshold=0.8,
            )

            # Verify search results
            self.assertEqual(len(res), 1)
            self.assertEqual(
                round(res[0].score, 4),
                0.9974,
            )
            self.assertEqual(
                res[0].metadata.content["text"],
                "This is a test document.",
            )
            self.assertEqual(res[0].metadata.doc_id, "doc1")
            self.assertEqual(res[0].metadata.chunk_id, 0)
            self.assertEqual(res[0].metadata.total_chunks, 2)

            # Verify ft.search was called
            self.assertTrue(mock_ft.search.called)

            # Test delete operation
            mock_ft.search = AsyncMock(
                return_value=mock_delete_search_response,
            )
            await store.delete(ids="doc1")

            # Verify delete was called with all keys in one batch
            self.assertTrue(mock_client_instance.delete.called)
            delete_call_args = mock_client_instance.delete.call_args_list[-1]
            self.assertEqual(len(delete_call_args[0][0]), 2)

            # Test drop_index
            mock_ft.list = AsyncMock(
                return_value=[b"test_idx"],
            )
            await store.drop_index()
            mock_ft.dropindex.assert_called_once()
            self.assertFalse(
                store._index_created,  # pylint: disable=W0212
            )
