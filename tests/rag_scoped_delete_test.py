# -*- coding: utf-8 -*-
"""Regression tests for scoped knowledge-base document deletion."""
from contextlib import AsyncExitStack
from importlib.util import find_spec
from types import SimpleNamespace
from unittest import skipUnless
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    ElasticsearchStore,
    KnowledgeBase,
    MilvusLiteStore,
    MongoDBStore,
    QdrantStore,
    VectorRecord,
)


def _knowledge_base(
    store: QdrantStore,
    metadata_filter: dict | None,
) -> KnowledgeBase:
    """Bind a handle without needing an embedding request for deletion."""
    return KnowledgeBase(
        name="test",
        description="Test knowledge base",
        embedding_model=SimpleNamespace(dimensions=3),
        vector_store=store,
        collection="shared",
        metadata_filter=metadata_filter,
    )


class _LegacyStore(QdrantStore):
    """Custom backend using the original, unscoped delete signature."""

    async def delete(self, collection: str, document_id: str) -> None:
        """Record deletions so a rejected scoped call cannot mutate data."""
        await self.get_client().delete(collection, document_id)


class LegacyDeleteTest(IsolatedAsyncioTestCase):
    """Old custom backends still work without a scope and fail closed."""

    async def test_legacy_backend_without_scope(self) -> None:
        """No metadata keyword is sent when the handle has no scope."""
        for scope in (None, {}):
            with self.subTest(scope=scope):
                store = _LegacyStore()
                client = SimpleNamespace(delete=AsyncMock())
                with (
                    patch.object(store, "has_collection", return_value=True),
                    patch.object(store, "get_client", return_value=client),
                ):
                    await _knowledge_base(store, scope).delete_document("doc")
                client.delete.assert_awaited_once_with("shared", "doc")

    async def test_legacy_backend_with_scope(self) -> None:
        """An unsupported scope is rejected before deleting anything."""
        store = _LegacyStore()
        client = SimpleNamespace(delete=AsyncMock())
        with (
            patch.object(store, "has_collection", return_value=True),
            patch.object(store, "get_client", return_value=client),
        ):
            with self.assertRaises(TypeError):
                await _knowledge_base(
                    store,
                    {"tenant": "alpha"},
                ).delete_document("doc")
        client.delete.assert_not_awaited()


@skipUnless(find_spec("qdrant_client"), "qdrant-client is required")
class QdrantScopedDeleteTest(IsolatedAsyncioTestCase):
    """Verify isolation against the real, in-memory Qdrant backend."""

    async def asyncSetUp(self) -> None:
        """Insert chunks spanning documents and two independent scope keys."""
        self._exit_stack = AsyncExitStack()
        self.store = await self._exit_stack.enter_async_context(
            QdrantStore(location=":memory:"),
        )
        await self.store.create_collection("shared", dimensions=3)
        self.scope = {"tenant": "alpha", "project": 2}
        records = []
        for document_id, index, metadata in (
            ("shared-doc", 0, self.scope),
            ("shared-doc", 1, {"tenant": "beta", "project": 2}),
            ("shared-doc", 2, {"tenant": "alpha", "project": 3}),
            ("alpha-doc", 0, self.scope),
            ("beta-doc", 0, {"tenant": "beta", "project": 2}),
        ):
            records.append(
                VectorRecord(
                    vector=[1.0, 0.0, 0.0],
                    document_id=document_id,
                    chunk=Chunk(
                        content=TextBlock(text=f"{document_id}-{index}"),
                        source=f"{document_id}.txt",
                        chunk_index=index,
                        total_chunks=3,
                        metadata=metadata,
                    ),
                ),
            )
        await self.store.insert("shared", records)

    async def asyncTearDown(self) -> None:
        """Release the ephemeral client."""
        await self._exit_stack.aclose()

    async def test_foreign_document_is_unchanged(self) -> None:
        """A scoped handle cannot remove an invisible foreign document."""
        before = await self.store.list_chunks("shared", "beta-doc")
        await _knowledge_base(self.store, self.scope).delete_document(
            "beta-doc",
        )
        after = await self.store.list_chunks("shared", "beta-doc")
        self.assertEqual(before, after)
        self.assertEqual(len(after), 1)

    async def test_delete_requires_document_and_every_scope_field(
        self,
    ) -> None:
        """Delete only chunks matching both the document and all scope keys."""
        await _knowledge_base(self.store, self.scope).delete_document(
            "shared-doc",
        )
        chunks = await self.store.list_chunks("shared", "shared-doc")
        self.assertEqual([chunk.chunk_index for chunk in chunks], [1, 2])
        self.assertEqual(
            len(await self.store.list_chunks("shared", "alpha-doc")),
            1,
        )
        await _knowledge_base(self.store, self.scope).delete_document(
            "alpha-doc",
        )
        self.assertEqual(
            await self.store.list_chunks("shared", "alpha-doc"),
            [],
        )

    async def test_unscoped_handle_deletes_all_document_chunks(self) -> None:
        """The default handle deletes documents across the whole collection."""
        await _knowledge_base(self.store, None).delete_document("shared-doc")
        self.assertEqual(
            await self.store.list_chunks("shared", "shared-doc"),
            [],
        )
        self.assertEqual(len(await self.store.list_documents("shared")), 2)


class BackendDeleteFilterTest(IsolatedAsyncioTestCase):
    """Check server-side delete predicates without starting remote services."""

    async def test_mongodb_delete_filter(self) -> None:
        """MongoDB combines the source document with metadata equality."""
        store = MongoDBStore(uri="mongodb://localhost", database="test")
        collection = SimpleNamespace(delete_many=AsyncMock())
        with patch.object(store, "_col", return_value=collection):
            await store.delete(
                "shared",
                "doc",
                metadata_filter={"tenant": "alpha", "project": 2},
            )
        collection.delete_many.assert_awaited_once_with(
            {
                "document_id": "doc",
                "$and": [
                    {"chunk.metadata.tenant": {"$eq": "alpha"}},
                    {"chunk.metadata.project": {"$eq": 2}},
                ],
            },
        )
        for scope in (None, {}):
            with self.subTest(scope=scope):
                collection.delete_many.reset_mock()
                with patch.object(store, "_col", return_value=collection):
                    await store.delete("shared", "doc", metadata_filter=scope)
                collection.delete_many.assert_awaited_once_with(
                    {"document_id": "doc"},
                )

    async def test_milvus_delete_filter(self) -> None:
        """Milvus keeps document escaping and ANDs both metadata fields."""
        store = MilvusLiteStore()
        client = SimpleNamespace(delete=Mock())
        with patch.object(store, "get_client", return_value=client):
            await store.delete(
                "shared",
                'doc"id',
                metadata_filter={"tenant": "alpha", "project": 2},
            )
        client.delete.assert_called_once_with(
            collection_name="shared",
            filter=(
                'document_id == "doc\\"id" and '
                'metadata["tenant"] == "alpha" and metadata["project"] == 2'
            ),
        )
        for scope in (None, {}):
            with self.subTest(scope=scope):
                client.delete.reset_mock()
                with patch.object(store, "get_client", return_value=client):
                    await store.delete("shared", "doc", metadata_filter=scope)
                client.delete.assert_called_once_with(
                    collection_name="shared",
                    filter='document_id == "doc"',
                )

    async def test_elasticsearch_delete_filter(self) -> None:
        """Elasticsearch uses a conjunction of exact-match filters."""
        store = ElasticsearchStore(hosts="http://localhost:9200")
        client = SimpleNamespace(delete_by_query=AsyncMock())
        with patch.object(store, "get_client", return_value=client):
            await store.delete(
                "shared",
                "doc",
                metadata_filter={"tenant": "alpha", "project": 2},
            )
        client.delete_by_query.assert_awaited_once_with(
            index="shared",
            query={
                "bool": {
                    "filter": [
                        {"term": {"document_id": "doc"}},
                        {"term": {"metadata.tenant": "alpha"}},
                        {"term": {"metadata.project": 2}},
                    ],
                },
            },
            conflicts="proceed",
            refresh=True,
        )
        for scope in (None, {}):
            with self.subTest(scope=scope):
                client.delete_by_query.reset_mock()
                with patch.object(store, "get_client", return_value=client):
                    await store.delete("shared", "doc", metadata_filter=scope)
                client.delete_by_query.assert_awaited_once_with(
                    index="shared",
                    query={"term": {"document_id": "doc"}},
                    conflicts="proceed",
                    refresh=True,
                )
