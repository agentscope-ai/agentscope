# -*- coding: utf-8 -*-
"""Test the RAG store implementations."""
import os
import types
import uuid
from unittest import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.rag import (
    QdrantStore,
    Document,
    DocMetadata,
    MilvusLiteStore,
    OceanBaseStore,
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

    async def asyncTearDown(self) -> None:
        """Clean up after tests."""
        # Remove Milvus Lite database file
        if os.path.exists("./milvus_demo.db"):
            os.remove("./milvus_demo.db")
