# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-function-docstring
"""Unit tests for the MongoDBStore class (mocked pymongo backend)."""
from __future__ import annotations

import math
from contextlib import AsyncExitStack
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from utils import AnyString

from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    MongoDBStore,
    VectorRecord,
    VectorSearchResult,
)


def _dump_results(results: list[VectorSearchResult]) -> list[dict]:
    """Convert search results into plain dicts for whole-structure
    comparison.

    Args:
        results (`list[VectorSearchResult]`):
            The search results to convert.

    Returns:
        `list[dict]`:
            The results as plain dicts.
    """
    return [result.model_dump() for result in results]


def _make_record(
    text: str,
    vector: list[float],
    document_id: str,
    chunk_index: int = 0,
    total_chunks: int = 1,
) -> VectorRecord:
    """Build a VectorRecord for testing.

    Args:
        text (`str`):
            The chunk text content.
        vector (`list[float]`):
            The embedding vector.
        document_id (`str`):
            The ID of the source document the record belongs to.
        chunk_index (`int`, defaults to ``0``):
            The chunk index within the document.
        total_chunks (`int`, defaults to ``1``):
            The total number of chunks in the document.

    Returns:
        `VectorRecord`:
            The constructed record.
    """
    return VectorRecord(
        vector=vector,
        document_id=document_id,
        chunk=Chunk(
            content=TextBlock(text=text),
            source=f"{document_id}.txt",
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        ),
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity aligned with Qdrant cosine scoring in tests."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class _FakeAsyncIterator:
    """Minimal async iterator for mocked MongoDB cursors."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self._index = 0

    def __aiter__(self) -> "_FakeAsyncIterator":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


class _FakeMongoCollection:
    """In-memory collection that implements the async API MongoDBStore uses."""

    def __init__(self, database: "_FakeMongoDatabase", name: str) -> None:
        self._database = database
        self._name = name
        self._docs: dict[str, dict[str, Any]] = {}
        self._index_queryable = False

    async def bulk_write(
        self,
        operations: list[Any],
        ordered: bool = False,
    ) -> None:
        del ordered
        for operation in operations:
            self._docs[operation._doc["_id"]] = dict(operation._doc)

    async def delete_many(self, filter_doc: dict[str, Any]) -> None:
        document_id = filter_doc["document_id"]
        to_remove = [
            doc_id
            for doc_id, doc in self._docs.items()
            if doc.get("document_id") == document_id
        ]
        for doc_id in to_remove:
            del self._docs[doc_id]

    async def create_search_index(self, model: Any) -> None:
        del model
        self._index_queryable = True

    async def list_search_indexes(self, name: str) -> _FakeAsyncIterator:
        del name
        return _FakeAsyncIterator([{"queryable": self._index_queryable}])

    async def aggregate(
        self,
        pipeline: list[dict[str, Any]],
    ) -> _FakeAsyncIterator:
        return _FakeAsyncIterator(self._run_aggregate(pipeline))

    async def drop(self) -> None:
        self._database._collections.pop(self._name, None)

    def _run_aggregate(
        self,
        pipeline: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if pipeline and "$vectorSearch" in pipeline[0]:
            return self._vector_search(pipeline[0]["$vectorSearch"])
        return self._list_documents(pipeline)

    def _vector_search(self, stage: dict[str, Any]) -> list[dict[str, Any]]:
        query_vector = stage["queryVector"]
        top_k = stage["limit"]
        metadata_filter = stage.get("filter")

        scored: list[dict[str, Any]] = []
        for doc in self._docs.values():
            if not self._matches_metadata_filter(doc, metadata_filter):
                continue
            scored.append(
                {
                    "document_id": doc["document_id"],
                    "chunk": doc["chunk"],
                    "score": _cosine_similarity(query_vector, doc["vector"]),
                },
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def _list_documents(
        self,
        pipeline: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        docs = list(self._docs.values())
        for stage in pipeline:
            if "$match" in stage:
                docs = [
                    doc
                    for doc in docs
                    if self._matches_match_stage(doc, stage["$match"])
                ]
            elif "$group" in stage:
                return self._group_documents(docs, stage["$group"])
        return []

    @staticmethod
    def _matches_match_stage(
        doc: dict[str, Any],
        match: dict[str, Any],
    ) -> bool:
        for key, expected in match.items():
            value = doc
            for part in key.split("."):
                value = value[part]
            if value != expected:
                return False
        return True

    @staticmethod
    def _matches_metadata_filter(
        doc: dict[str, Any],
        metadata_filter: dict[str, Any] | None,
    ) -> bool:
        if not metadata_filter:
            return True

        for clause in metadata_filter.get("$and", []):
            for key, condition in clause.items():
                expected = condition["$eq"]
                value = doc
                for part in key.split("."):
                    value = value[part]
                if value != expected:
                    return False
        return True

    @staticmethod
    def _group_documents(
        docs: list[dict[str, Any]],
        group_stage: dict[str, Any],
    ) -> list[dict[str, Any]]:
        del group_stage
        grouped: dict[str, dict[str, Any]] = {}
        for doc in docs:
            document_id = doc["document_id"]
            if document_id not in grouped:
                grouped[document_id] = {
                    "_id": document_id,
                    "source": doc["chunk"]["source"],
                    "metadata": doc["chunk"].get("metadata") or {},
                    "chunk_count": 0,
                }
            grouped[document_id]["chunk_count"] += 1
        return list(grouped.values())


class _FakeMongoDatabase:
    """In-memory database handle."""

    def __init__(self) -> None:
        self._collections: dict[str, _FakeMongoCollection] = {}

    async def list_collection_names(self) -> list[str]:
        return list(self._collections.keys())

    async def create_collection(self, name: str) -> _FakeMongoCollection:
        collection = _FakeMongoCollection(self, name)
        self._collections[name] = collection
        return collection

    def __getitem__(self, name: str) -> _FakeMongoCollection:
        if name not in self._collections:
            self._collections[name] = _FakeMongoCollection(self, name)
        return self._collections[name]


class _FakeMongoClient:
    """In-memory async MongoDB client."""

    def __init__(self, database_name: str) -> None:
        self._database_name = database_name
        self._databases: dict[str, _FakeMongoDatabase] = {}

    def __getitem__(self, database_name: str) -> _FakeMongoDatabase:
        if database_name not in self._databases:
            self._databases[database_name] = _FakeMongoDatabase()
        return self._databases[database_name]

    async def close(self) -> None:
        self._databases.clear()


class MongoDBStoreTest(IsolatedAsyncioTestCase):
    """The test cases for the MongoDBStore class."""

    async def asyncSetUp(self) -> None:
        """Create a MongoDB store with a mocked pymongo client."""
        self._fake_client = _FakeMongoClient("test-db")
        self._client_patcher = patch.object(
            MongoDBStore,
            "get_client",
            return_value=self._fake_client,
        )
        self._client_patcher.start()

        self._exit_stack = AsyncExitStack()
        self.store = MongoDBStore(uri="mongodb://mock", database="test-db")
        await self._exit_stack.enter_async_context(self.store)

    async def asyncTearDown(self) -> None:
        """Close the store and stop patches after each test."""
        await self._exit_stack.aclose()
        self._client_patcher.stop()

    async def test_collection_lifecycle(self) -> None:
        """Collections can be created, checked, and deleted."""
        self.assertEqual(await self.store.has_collection("kb-1"), False)

        await self.store.create_collection("kb-1", dimensions=3)
        self.assertEqual(await self.store.has_collection("kb-1"), True)

        # Creating an existing collection is a no-op
        await self.store.create_collection("kb-1", dimensions=3)
        self.assertEqual(await self.store.has_collection("kb-1"), True)

        await self.store.delete_collection("kb-1")
        self.assertEqual(await self.store.has_collection("kb-1"), False)

    async def test_insert_and_search(self) -> None:
        """Inserted records are searchable, ordered by similarity."""
        await self.store.create_collection("kb-1", dimensions=3)
        await self.store.insert(
            "kb-1",
            [
                _make_record(
                    "Hello world!",
                    [1.0, 0.0, 0.0],
                    document_id="doc-1",
                    chunk_index=0,
                    total_chunks=2,
                ),
                _make_record(
                    "Goodbye world!",
                    [0.0, 1.0, 0.0],
                    document_id="doc-1",
                    chunk_index=1,
                    total_chunks=2,
                ),
            ],
        )

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=2,
        )

        self.assertEqual(
            _dump_results(results),
            [
                {
                    "score": 1.0,
                    "document_id": "doc-1",
                    "chunk": {
                        "content": {
                            "type": "text",
                            "text": "Hello world!",
                            "id": AnyString(),
                        },
                        "source": "doc-1.txt",
                        "chunk_index": 0,
                        "total_chunks": 2,
                        "metadata": {},
                    },
                },
                {
                    "score": 0.0,
                    "document_id": "doc-1",
                    "chunk": {
                        "content": {
                            "type": "text",
                            "text": "Goodbye world!",
                            "id": AnyString(),
                        },
                        "source": "doc-1.txt",
                        "chunk_index": 1,
                        "total_chunks": 2,
                        "metadata": {},
                    },
                },
            ],
        )

    async def test_search_top_k(self) -> None:
        """top_k limits the number of returned results."""
        await self.store.create_collection("kb-1", dimensions=3)
        await self.store.insert(
            "kb-1",
            [
                _make_record("A", [1.0, 0.0, 0.0], document_id="doc-1"),
                _make_record("B", [0.9, 0.1, 0.0], document_id="doc-2"),
                _make_record("C", [0.0, 0.0, 1.0], document_id="doc-3"),
            ],
        )

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=1,
        )

        self.assertEqual(
            _dump_results(results),
            [
                {
                    "score": 1.0,
                    "document_id": "doc-1",
                    "chunk": {
                        "content": {
                            "type": "text",
                            "text": "A",
                            "id": AnyString(),
                        },
                        "source": "doc-1.txt",
                        "chunk_index": 0,
                        "total_chunks": 1,
                        "metadata": {},
                    },
                },
            ],
        )

    async def test_delete_by_document_id(self) -> None:
        """delete removes all records of one document only."""
        await self.store.create_collection("kb-1", dimensions=3)
        await self.store.insert(
            "kb-1",
            [
                _make_record(
                    "doc1-chunk0",
                    [1.0, 0.0, 0.0],
                    document_id="doc-1",
                    chunk_index=0,
                    total_chunks=2,
                ),
                _make_record(
                    "doc1-chunk1",
                    [0.9, 0.1, 0.0],
                    document_id="doc-1",
                    chunk_index=1,
                    total_chunks=2,
                ),
                _make_record(
                    "doc2-chunk0",
                    [0.0, 1.0, 0.0],
                    document_id="doc-2",
                ),
            ],
        )

        await self.store.delete("kb-1", document_id="doc-1")

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
        )

        self.assertEqual(
            _dump_results(results),
            [
                {
                    "score": 0.0,
                    "document_id": "doc-2",
                    "chunk": {
                        "content": {
                            "type": "text",
                            "text": "doc2-chunk0",
                            "id": AnyString(),
                        },
                        "source": "doc-2.txt",
                        "chunk_index": 0,
                        "total_chunks": 1,
                        "metadata": {},
                    },
                },
            ],
        )

    async def test_insert_empty_records(self) -> None:
        """Inserting an empty record list is a no-op."""
        await self.store.create_collection("kb-1", dimensions=3)
        await self.store.insert("kb-1", [])

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
        )

        self.assertEqual(_dump_results(results), [])

    async def test_list_documents_aggregates_by_document_id(self) -> None:
        """list_documents groups chunks by document_id."""
        await self.store.create_collection("kb-1", dimensions=3)

        def _record_with_metadata(
            text: str,
            document_id: str,
            metadata: dict,
            chunk_index: int = 0,
            total_chunks: int = 1,
        ) -> VectorRecord:
            return VectorRecord(
                vector=[1.0, 0.0, 0.0],
                document_id=document_id,
                chunk=Chunk(
                    content=TextBlock(text=text),
                    source=metadata.get("filename", f"{document_id}.txt"),
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    metadata=metadata,
                ),
            )

        await self.store.insert(
            "kb-1",
            [
                _record_with_metadata(
                    "A",
                    "doc-1",
                    {"filename": "alpha.txt", "media_type": "text/plain"},
                    0,
                    2,
                ),
                _record_with_metadata(
                    "B",
                    "doc-1",
                    {"filename": "alpha.txt", "media_type": "text/plain"},
                    1,
                    2,
                ),
                _record_with_metadata(
                    "C",
                    "doc-2",
                    {"filename": "beta.md", "media_type": "text/markdown"},
                    0,
                    1,
                ),
            ],
        )

        summaries = sorted(
            await self.store.list_documents("kb-1"),
            key=lambda summary: summary.document_id,
        )
        self.assertEqual(
            [summary.model_dump() for summary in summaries],
            [
                {
                    "document_id": "doc-1",
                    "source": "alpha.txt",
                    "chunk_count": 2,
                    "metadata": {
                        "filename": "alpha.txt",
                        "media_type": "text/plain",
                    },
                },
                {
                    "document_id": "doc-2",
                    "source": "beta.md",
                    "chunk_count": 1,
                    "metadata": {
                        "filename": "beta.md",
                        "media_type": "text/markdown",
                    },
                },
            ],
        )

    async def test_search_metadata_filter(self) -> None:
        """search applies the metadata_filter as a payload predicate."""
        await self.store.create_collection("kb-1", dimensions=3)

        def _record(
            text: str,
            document_id: str,
            kb_scope: str,
        ) -> VectorRecord:
            return VectorRecord(
                vector=[1.0, 0.0, 0.0],
                document_id=document_id,
                chunk=Chunk(
                    content=TextBlock(text=text),
                    source=f"{document_id}.txt",
                    chunk_index=0,
                    total_chunks=1,
                    metadata={"kb_scope": kb_scope},
                ),
            )

        await self.store.insert(
            "kb-1",
            [
                _record("A", "doc-1", "kb-a"),
                _record("B", "doc-2", "kb-b"),
            ],
        )

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"kb_scope": "kb-a"},
        )
        self.assertEqual(
            _dump_results(results),
            [
                {
                    "score": 1.0,
                    "document_id": "doc-1",
                    "chunk": {
                        "content": {
                            "type": "text",
                            "text": "A",
                            "id": AnyString(),
                        },
                        "source": "doc-1.txt",
                        "chunk_index": 0,
                        "total_chunks": 1,
                        "metadata": {"kb_scope": "kb-a"},
                    },
                },
            ],
        )

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"kb_scope": "kb-b"},
        )
        self.assertEqual(
            _dump_results(results),
            [
                {
                    "score": 1.0,
                    "document_id": "doc-2",
                    "chunk": {
                        "content": {
                            "type": "text",
                            "text": "B",
                            "id": AnyString(),
                        },
                        "source": "doc-2.txt",
                        "chunk_index": 0,
                        "total_chunks": 1,
                        "metadata": {"kb_scope": "kb-b"},
                    },
                },
            ],
        )
