# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-function-docstring
"""Unit tests for the ChromaStore class."""

import json
import sys
import types
import unittest
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from agentscope.message import TextBlock
from agentscope.rag import ChromaStore, Chunk, VectorRecord
from agentscope.rag._vdb._chroma import (
    _CHUNK_INDEX_KEY,
    _DOCUMENT_ID_KEY,
    _DIMENSIONS_KEY,
    _METADATA_PREFIX,
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
            total_chunks=3,
            metadata=metadata or {},
        ),
    )


class _FakeCollection:
    """Minimal synchronous Chroma collection used by the tests."""

    def __init__(self, name: str, metadata: dict[str, Any]) -> None:
        self.name = name
        self.metadata = metadata
        self.upsert = Mock()
        self.delete = Mock()
        self.query = Mock()
        self.get = Mock()


class _FakeClient:
    """Minimal synchronous Chroma client used by the tests."""

    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}
        self.get_or_create_collection = Mock(
            side_effect=self._get_or_create_collection,
        )
        self.get_collection = Mock(side_effect=self._get_collection)
        self.delete_collection = Mock(side_effect=self._delete_collection)
        self.list_collections = Mock(side_effect=self._list_collections)
        self.close = Mock()

    def _get_or_create_collection(
        self,
        name: str,
        metadata: dict[str, Any],
        embedding_function: None,
    ) -> _FakeCollection:
        del embedding_function
        return self.collections.setdefault(
            name,
            _FakeCollection(name, metadata),
        )

    def _get_collection(
        self,
        name: str,
        embedding_function: None,
    ) -> _FakeCollection:
        del embedding_function
        return self.collections[name]

    def _delete_collection(self, name: str) -> None:
        del self.collections[name]

    def _list_collections(self) -> list[_FakeCollection]:
        return list(self.collections.values())


class ChromaStoreTest(IsolatedAsyncioTestCase):
    """The Chroma vector-store contract tests."""

    async def asyncSetUp(self) -> None:
        self.client = _FakeClient()
        self.client_patcher = patch.object(
            ChromaStore,
            "get_client",
            return_value=self.client,
        )
        self.client_patcher.start()
        self.store = ChromaStore()

    async def asyncTearDown(self) -> None:
        self.client_patcher.stop()

    async def test_collection_lifecycle_and_dimension_validation(self) -> None:
        self.assertFalse(await self.store.has_collection("kb-1"))

        await self.store.create_collection("kb-1", dimensions=3)
        self.assertTrue(await self.store.has_collection("kb-1"))
        self.client.get_or_create_collection.assert_called_once_with(
            name="kb-1",
            metadata={"hnsw:space": "cosine", _DIMENSIONS_KEY: 3},
            embedding_function=None,
        )

        await self.store.create_collection("kb-1", dimensions=3)
        self.assertEqual(self.client.get_or_create_collection.call_count, 2)

        with self.assertRaisesRegex(ValueError, "dimension mismatch"):
            await self.store.create_collection("kb-1", dimensions=4)

        await self.store.delete_collection("kb-1")
        self.assertFalse(await self.store.has_collection("kb-1"))

    async def test_insert_uses_stable_ids_and_batches(self) -> None:
        store = ChromaStore(batch_size=1)
        await store.create_collection("kb-1", dimensions=3)
        records = [_record("doc-1", 0), _record("doc-1", 1)]

        await store.insert("kb-1", records)
        await store.insert("kb-1", records)

        collection = self.client.collections["kb-1"]
        first_call = collection.upsert.call_args_list[0]
        retried_first_call = collection.upsert.call_args_list[2]
        self.assertEqual(first_call.kwargs, retried_first_call.kwargs)
        self.assertEqual(len(first_call.kwargs["ids"]), 1)
        self.assertEqual(collection.upsert.call_count, 4)
        self.assertNotEqual(
            first_call.kwargs["ids"][0],
            collection.upsert.call_args_list[1].kwargs["ids"][0],
        )
        self.assertEqual(
            json.loads(first_call.kwargs["documents"][0])["chunk_index"],
            0,
        )

    async def test_delete_by_document_id(self) -> None:
        await self.store.create_collection("kb-1", dimensions=3)
        await self.store.delete("kb-1", "doc-1")

        collection = self.client.collections["kb-1"]
        collection.delete.assert_called_once_with(
            where={_DOCUMENT_ID_KEY: "doc-1"},
        )

    async def test_context_exit_closes_the_client(self) -> None:
        self.store._client = self.client

        await self.store.__aexit__(None, None, None)

        self.client.close.assert_called_once_with()
        self.assertIsNone(self.store._client)

    async def test_search_normalizes_cosine_distance_and_filters_metadata(
        self,
    ) -> None:
        await self.store.create_collection("kb-1", dimensions=3)
        record = _record("doc-1", 0, {"tenant": "bank-a"})
        collection = self.client.collections["kb-1"]
        collection.query.return_value = {
            "documents": [[self.store._serialize_chunk(record.chunk)]],
            "metadatas": [[{_DOCUMENT_ID_KEY: "doc-1"}]],
            "distances": [[0.1]],
        }

        results = await self.store.search(
            "kb-1",
            [1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"tenant": "bank-a"},
        )

        self.assertAlmostEqual(results[0].score, 0.9)
        self.assertEqual(results[0].document_id, "doc-1")
        self.assertEqual(results[0].chunk.metadata, {"tenant": "bank-a"})
        collection.query.assert_called_once_with(
            query_embeddings=[[1.0, 0.0, 0.0]],
            n_results=5,
            where={f"{_METADATA_PREFIX}tenant": '"bank-a"'},
            include=["documents", "metadatas", "distances"],
        )

    async def test_search_normalizes_l2_distance(self) -> None:
        store = ChromaStore(distance="l2")
        await store.create_collection("kb-1", dimensions=3)
        record = _record("doc-1", 0)
        collection = self.client.collections["kb-1"]
        collection.query.return_value = {
            "documents": [[store._serialize_chunk(record.chunk)]],
            "metadatas": [[{_DOCUMENT_ID_KEY: "doc-1"}]],
            "distances": [[4.0]],
        }

        results = await store.search("kb-1", [1.0, 0.0, 0.0])

        self.assertEqual(results[0].score, -4.0)

    def test_metadata_filter_encodes_structured_values(self) -> None:
        self.assertEqual(
            ChromaStore._build_metadata_filter(
                {"labels": ["rag", "chroma"], "page": 2},
            ),
            {
                "$and": [
                    {f"{_METADATA_PREFIX}labels": '["rag","chroma"]'},
                    {f"{_METADATA_PREFIX}page": "2"},
                ],
            },
        )

    async def test_list_documents_paginates_and_aggregates(self) -> None:
        store = ChromaStore(batch_size=2)
        await store.create_collection("kb-1", dimensions=3)
        collection = self.client.collections["kb-1"]
        first = [_record("doc-1", 0), _record("doc-1", 1)]
        second = [_record("doc-2", 0)]
        collection.get.side_effect = [
            {
                "documents": [store._serialize_chunk(r.chunk) for r in first],
                "metadatas": [
                    {_DOCUMENT_ID_KEY: r.document_id} for r in first
                ],
            },
            {
                "documents": [store._serialize_chunk(r.chunk) for r in second],
                "metadatas": [
                    {_DOCUMENT_ID_KEY: r.document_id} for r in second
                ],
            },
        ]

        summaries = await store.list_documents("kb-1")

        summaries_by_id = {
            summary.document_id: summary for summary in summaries
        }
        self.assertEqual(summaries_by_id["doc-1"].chunk_count, 2)
        self.assertEqual(summaries_by_id["doc-1"].source, "doc-1.txt")
        self.assertEqual(summaries_by_id["doc-2"].chunk_count, 1)
        self.assertEqual(collection.get.call_count, 2)
        self.assertEqual(collection.get.call_args_list[1].kwargs["offset"], 2)

    async def test_list_chunks_filters_and_orders_by_chunk_index(self) -> None:
        await self.store.create_collection("kb-1", dimensions=3)
        collection = self.client.collections["kb-1"]
        records = [
            _record("doc-1", 2, {"tenant": "bank-a"}),
            _record("doc-1", 0, {"tenant": "bank-a"}),
            _record("doc-1", 1, {"tenant": "bank-a"}),
        ]
        collection.get.return_value = {
            "documents": [
                self.store._serialize_chunk(r.chunk) for r in records
            ],
        }

        chunks = await self.store.list_chunks(
            "kb-1",
            "doc-1",
            offset=0,
            limit=3,
            metadata_filter={"tenant": "bank-a"},
        )

        self.assertEqual([chunk.chunk_index for chunk in chunks], [0, 1, 2])
        where = collection.get.call_args.kwargs["where"]
        self.assertEqual(
            where,
            {
                "$and": [
                    {_DOCUMENT_ID_KEY: "doc-1"},
                    {_CHUNK_INDEX_KEY: {"$gte": 0}},
                    {_CHUNK_INDEX_KEY: {"$lt": 3}},
                    {f"{_METADATA_PREFIX}tenant": '"bank-a"'},
                ],
            },
        )

    async def test_client_constructors_are_lazy_and_configurable(self) -> None:
        self.client_patcher.stop()
        fake_clients = types.SimpleNamespace(
            PersistentClient=Mock(return_value="persistent"),
            HttpClient=Mock(return_value="http"),
            EphemeralClient=Mock(return_value="ephemeral"),
        )
        with patch.dict(sys.modules, {"chromadb": fake_clients}):
            local_store = ChromaStore(path="C:\\data\\chroma")
            self.assertEqual(local_store.get_client(), "persistent")
            fake_clients.PersistentClient.assert_called_once_with(
                path="C:\\data\\chroma",
                tenant="default_tenant",
                database="default_database",
            )

            remote_store = ChromaStore(
                host="chroma.example",
                port=8443,
                ssl=True,
                headers={"Authorization": "Bearer token"},
            )
            self.assertEqual(remote_store.get_client(), "http")
            fake_clients.HttpClient.assert_called_once_with(
                host="chroma.example",
                port=8443,
                ssl=True,
                tenant="default_tenant",
                database="default_database",
                headers={"Authorization": "Bearer token"},
            )


if __name__ == "__main__":
    unittest.main()
