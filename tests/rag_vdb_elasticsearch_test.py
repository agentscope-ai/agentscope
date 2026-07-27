# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-function-docstring
"""Unit tests for the ElasticsearchStore class."""
from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    ElasticsearchStore,
    VectorRecord,
)


def _record(
    document_id: str,
    chunk_index: int,
    metadata: dict[str, Any] | None = None,
) -> VectorRecord:
    return VectorRecord(
        vector=[1.0, 0.0, 0.0],
        document_id=document_id,
        chunk=Chunk(
            content=TextBlock(text=f"chunk-{chunk_index}"),
            source=f"{document_id}.txt",
            chunk_index=chunk_index,
            total_chunks=2,
            metadata=metadata or {},
        ),
    )


class _FakeIndices:
    """Minimal asynchronous indices namespace."""

    def __init__(self) -> None:
        self.exists = AsyncMock(return_value=False)
        self.create = AsyncMock()
        self.delete = AsyncMock()


class _FakeClient:
    """Minimal asynchronous Elasticsearch client."""

    def __init__(self) -> None:
        self.indices = _FakeIndices()
        self.bulk = AsyncMock(return_value={"errors": False, "items": []})
        self.delete_by_query = AsyncMock()
        self.search = AsyncMock()
        self.close = AsyncMock()


class ElasticsearchStoreTest(IsolatedAsyncioTestCase):
    """Elasticsearch vector-store contract tests."""

    async def asyncSetUp(self) -> None:
        self.client = _FakeClient()
        self.client_patcher = patch.object(
            ElasticsearchStore,
            "get_client",
            return_value=self.client,
        )
        self.client_patcher.start()
        self.exit_stack = AsyncExitStack()
        self.store = ElasticsearchStore(hosts="http://localhost:9200")
        await self.exit_stack.enter_async_context(self.store)

    async def asyncTearDown(self) -> None:
        await self.exit_stack.aclose()
        self.client_patcher.stop()

    async def test_collection_lifecycle(self) -> None:
        self.assertFalse(await self.store.has_collection("kb-1"))

        await self.store.create_collection("kb-1", dimensions=3)

        self.client.indices.create.assert_awaited_once_with(
            index="kb-1",
            mappings={
                "dynamic": False,
                "properties": {
                    "vector": {
                        "type": "dense_vector",
                        "dims": 3,
                        "index": True,
                        "similarity": "cosine",
                    },
                    "document_id": {"type": "keyword"},
                    "chunk": {"type": "object", "enabled": False},
                    "metadata": {"type": "object", "dynamic": "runtime"},
                },
            },
        )

        await self.store.delete_collection("kb-1")
        self.client.indices.delete.assert_awaited_once_with(index="kb-1")

    async def test_insert_uses_stable_ids(self) -> None:
        records = [_record("doc-1", 0), _record("doc-1", 1)]
        await self.store.insert("kb-1", records)
        first_operations = self.client.bulk.await_args.kwargs["operations"]

        await self.store.insert("kb-1", records)
        second_operations = self.client.bulk.await_args.kwargs["operations"]

        self.assertEqual(first_operations, second_operations)
        self.assertEqual(first_operations[0]["index"]["_index"], "kb-1")
        self.assertNotEqual(
            first_operations[0]["index"]["_id"],
            first_operations[2]["index"]["_id"],
        )
        self.assertEqual(first_operations[1]["document_id"], "doc-1")

    async def test_insert_empty_records_is_noop(self) -> None:
        await self.store.insert("kb-1", [])
        self.client.bulk.assert_not_awaited()

    async def test_refresh_policy_can_disable_write_refreshes(self) -> None:
        store = ElasticsearchStore(
            hosts="http://localhost:9200",
            refresh=False,
        )

        await store.insert("kb-1", [_record("doc-1", 0)])
        await store.delete("kb-1", "doc-1")

        self.assertIs(self.client.bulk.await_args.kwargs["refresh"], False)
        self.assertIs(
            self.client.delete_by_query.await_args.kwargs["refresh"],
            False,
        )

    async def test_insert_surfaces_bulk_item_failures(self) -> None:
        self.client.bulk.return_value = {
            "errors": True,
            "items": [{"index": {"error": {"type": "mapper_error"}}}],
        }

        with self.assertRaisesRegex(RuntimeError, "1 record"):
            await self.store.insert("kb-1", [_record("doc-1", 0)])

    async def test_delete_by_document_id(self) -> None:
        await self.store.delete("kb-1", "doc-1")

        self.client.delete_by_query.assert_awaited_once_with(
            index="kb-1",
            query={"term": {"document_id": "doc-1"}},
            conflicts="proceed",
            refresh=True,
        )

    async def test_search_with_metadata_filter(self) -> None:
        chunk = _record("doc-1", 0, {"tenant": "bank-a"}).chunk
        self.client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_score": 0.95,
                        "_source": {
                            "document_id": "doc-1",
                            "chunk": chunk.model_dump(mode="json"),
                        },
                    },
                ],
            },
        }

        results = await self.store.search(
            "kb-1",
            [1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"tenant": "bank-a"},
        )

        self.client.search.assert_awaited_once_with(
            index="kb-1",
            size=5,
            knn={
                "field": "vector",
                "query_vector": [1.0, 0.0, 0.0],
                "k": 5,
                "num_candidates": 100,
                "filter": [{"term": {"metadata.tenant": "bank-a"}}],
            },
            source_includes=["document_id", "chunk"],
        )
        self.assertEqual(results[0].document_id, "doc-1")
        # Elasticsearch maps cosine to (1 + cosine) / 2.  The store
        # normalizes it back to the raw cosine used by other backends.
        self.assertAlmostEqual(results[0].score, 0.9)

    async def test_search_rejects_top_k_above_elasticsearch_limit(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "10000"):
            await self.store.search("kb-1", [1.0, 0.0, 0.0], top_k=10_001)
        self.client.search.assert_not_awaited()

    async def test_list_documents_uses_composite_pagination(self) -> None:
        chunk = _record("doc-1", 0, {"tenant": "bank-a"}).chunk
        self.client.search.side_effect = [
            {
                "aggregations": {
                    "documents": {
                        "buckets": [
                            {
                                "key": {"document_id": "doc-1"},
                                "doc_count": 2,
                                "sample": {
                                    "hits": {
                                        "hits": [
                                            {
                                                "_source": {
                                                    "chunk": chunk.model_dump(
                                                        mode="json",
                                                    ),
                                                },
                                            },
                                        ],
                                    },
                                },
                            },
                        ],
                        "after_key": {"document_id": "doc-1"},
                    },
                },
            },
            {"aggregations": {"documents": {"buckets": []}}},
        ]

        summaries = await self.store.list_documents(
            "kb-1",
            metadata_filter={"tenant": "bank-a"},
        )

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].document_id, "doc-1")
        self.assertEqual(summaries[0].chunk_count, 2)
        self.assertEqual(summaries[0].metadata, {"tenant": "bank-a"})
        self.assertEqual(self.client.search.await_count, 2)
        second_query = self.client.search.await_args_list[1].kwargs
        self.assertEqual(
            second_query["aggs"]["documents"]["composite"]["after"],
            {"document_id": "doc-1"},
        )
