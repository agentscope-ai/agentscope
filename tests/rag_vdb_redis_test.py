# -*- coding: utf-8 -*-
"""Unit tests for the RedisStore class.

Uses an in-memory fake Redis client (similar to the MongoDBStore test
pattern) because fakeredis 2.36.2 does not implement RediSearch (FT.*)
commands.
"""

import re
import types
from contextlib import AsyncExitStack
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import numpy as np

from utils import AnyString

from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    RedisStore,
    VectorRecord,
    VectorSearchResult,
)

# ------------------------------------------------------------------
# Module-level helpers (same pattern as rag_vdb_qdrant_test.py)
# ------------------------------------------------------------------


def _dump_results(results: list[VectorSearchResult]) -> list[dict]:
    """Convert search results into plain dicts for whole-structure
    comparison.

    Args:
        results (`list[VectorSearchResult]`):
            The search results to convert.

    Returns:
        `list[dict]`: The results as plain dicts.
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
        text (`str`): The chunk text content.
        vector (`list[float]`): The embedding vector.
        document_id (`str`): The ID of the source document.
        chunk_index (`int`, defaults to ``0``): The chunk index.
        total_chunks (`int`, defaults to ``1``): Total chunks in doc.

    Returns:
        `VectorRecord`: The constructed record.
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


# ------------------------------------------------------------------
# Fake Redis client — simulates the async API surface RedisStore uses
# ------------------------------------------------------------------

_KNN_RE = re.compile(
    r"^(.*)=>\s*\[KNN\s+(\d+)\s+@embedding\s+\$vec\s+AS\s+(\S+)\]$",
)
_TAG_FILTER_RE = re.compile(r"@(\S+):\{([^}]*)\}")


# pylint: disable=protected-access,unused-argument


class _FakePipeline:
    """Async context manager that collects HSET calls and flushes them
    to the parent client on ``execute()``."""

    def __init__(self, client: "_FakeRedisClient") -> None:
        self._client = client
        self._commands: list[tuple[str, str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    def hset(self, key: str, mapping: dict[str, Any]) -> None:
        """Queue an HSET."""
        self._commands.append(("hset", key, mapping))

    async def execute(self) -> None:
        """Commit all queued commands to the client's data store."""
        for _, key, mapping in self._commands:
            self._client._docs[key] = dict(mapping)


class _FakeRedisIndex:
    """Simulates one RediSearch index (created via ``FT.CREATE``)."""

    def __init__(
        self,
        client: "_FakeRedisClient",
        name: str,
    ) -> None:
        self._client = client
        self._name = name
        self._exists = False
        self._fields: dict[str, str] = {}  # field_name → field_type
        self._cursor_rows: dict[int, tuple[list[dict[str, Any]], int]] = {}
        self._next_cursor_id = 1

    # -- lifecycle -------------------------------------------------------

    async def create_index(
        self,
        fields: list[Any],
        definition: Any | None = None,
    ) -> None:
        """Record the schema fields."""
        for f in fields:
            if hasattr(f, "name"):
                self._fields[f.name] = type(f).__name__
        self._exists = True

    async def alter_schema_add(self, *fields: Any) -> None:
        """Add TAG fields to the schema (FT.ALTER)."""
        for f in fields:
            if hasattr(f, "name"):
                self._fields[f.name] = type(f).__name__

    async def info(self) -> dict[str, Any]:
        """Return index info or raise on unknown index."""
        if not self._exists:
            import redis.exceptions

            raise redis.exceptions.ResponseError("Unknown Index name")
        return {
            "index_name": self._name,
            "num_docs": len(self._matching_keys()),
        }

    async def dropindex(self, delete_documents: bool = False) -> None:
        """Remove the index and optionally its documents."""
        if delete_documents:
            prefix = f"{self._name}:doc:"
            to_delete = [k for k in self._client._docs if k.startswith(prefix)]
            for k in to_delete:
                del self._client._docs[k]
        self._exists = False

    # -- search ---------------------------------------------------------

    async def search(
        self,
        query: Any,
        query_params: dict[str, Any] | None = None,
    ) -> Any:
        """Simulate ``FT.SEARCH`` with KNN vector search or pure filter.

        Parses the query string: if it's a hybrid KNN query, computes
        cosine similarity; otherwise treats it as a pure TAG filter
        (used by :meth:`RedisStore.delete`).
        """
        qs = query.query_string()

        m = _KNN_RE.match(qs)
        if m:
            return await self._knn_search(m, query, query_params or {})
        return self._filter_search(qs, query)

    async def _knn_search(
        self,
        m: re.Match,
        query: Any,
        query_params: dict[str, Any],
    ) -> Any:
        """Hybrid KNN search — parse filter prefix, compute cosine
        similarity, return top-k sorted by distance ascending."""
        filter_part = m.group(1).strip()
        k = int(m.group(2))
        distance_alias = m.group(3)
        vec_bytes = list(query_params.values())[0] if query_params else b""
        paging_offset = query._offset
        paging_num = query._num

        # Parse metadata filter from the prefix part
        tag_filters: dict[str, str] = {}
        if filter_part and filter_part != "*":
            for tf_m in _TAG_FILTER_RE.finditer(filter_part):
                tag_filters[tf_m.group(1)] = tf_m.group(2)

        query_vec = np.frombuffer(vec_bytes, dtype=np.float32)
        scored: list[tuple[float, str]] = []

        for key, doc in self._client._docs.items():
            if not key.startswith(f"{self._name}:doc:"):
                continue
            if not self._matches_filters(doc, tag_filters):
                continue

            emb_bytes = doc.get("embedding", b"")
            if isinstance(emb_bytes, str):
                emb_bytes = emb_bytes.encode("latin-1")
            emb = np.frombuffer(emb_bytes, dtype=np.float32)

            norm_q = np.linalg.norm(query_vec)
            norm_d = np.linalg.norm(emb)
            if norm_q == 0 or norm_d == 0:
                sim = 0.0
            else:
                sim = float(np.dot(query_vec, emb) / (norm_q * norm_d))
            distance = 1.0 - sim
            scored.append((distance, key))

        scored.sort(key=lambda x: x[0])
        page = scored[paging_offset : paging_offset + paging_num]
        page = page[:k]

        docs = []
        for dist, key in page:
            doc = self._client._docs[key]
            docs.append(
                types.SimpleNamespace(
                    id=key,
                    content=doc.get("content", ""),
                    document_id=doc.get("document_id", ""),
                    **{distance_alias: str(dist)},
                ),
            )

        return _result(docs, len(scored))

    def _filter_search(
        self,
        qs: str,
        query: Any,
    ) -> Any:
        """Pure filter search (no KNN) — used for delete-by-document-id
        and other metadata lookups."""
        tag_filters: dict[str, str] = {}
        for tf_m in _TAG_FILTER_RE.finditer(qs):
            tag_filters[tf_m.group(1)] = tf_m.group(2)

        paging_offset = query._offset
        paging_num = query._num

        matching_keys = []
        for key, doc in self._client._docs.items():
            if not key.startswith(f"{self._name}:doc:"):
                continue
            if self._matches_filters(doc, tag_filters):
                matching_keys.append(key)

        total = len(matching_keys)
        page_keys = matching_keys[paging_offset : paging_offset + paging_num]
        docs = []
        for key in page_keys:
            doc = self._client._docs[key]
            docs.append(
                types.SimpleNamespace(
                    id=key,
                    content=doc.get("content", ""),
                    document_id=doc.get("document_id", ""),
                ),
            )

        return _result(docs, total)

    # -- aggregate ------------------------------------------------------

    async def aggregate(self, request: Any) -> Any:
        """Simulate ``FT.AGGREGATE`` with GROUPBY on document_id.

        Groups matching documents by ``document_id``, counting chunks
        and collecting the first chunk's content per group. Supports the
        cursor reads used by :meth:`RedisStore.list_documents`.
        """
        if hasattr(request, "cid"):
            return self._read_cursor(request.cid)

        args = request.build_args() if hasattr(request, "build_args") else []
        query_str = args[0] if args else "*"

        # Parse metadata filter
        tag_filters: dict[str, str] = {}
        if query_str and query_str != "*":
            query_str = query_str.strip("() ")
            for tf_m in _TAG_FILTER_RE.finditer(query_str):
                tag_filters[tf_m.group(1)] = tf_m.group(2)

        groups: dict[str, dict[str, Any]] = {}
        prefix = f"{self._name}:doc:"

        for key, doc in self._client._docs.items():
            if not key.startswith(prefix):
                continue
            if not self._matches_filters(doc, tag_filters):
                continue

            doc_id = doc.get("document_id", "")
            entry = groups.get(doc_id)
            if entry is None:
                groups[doc_id] = {
                    "document_id": doc_id,
                    "chunk_count": 1,
                    "sample_content": doc.get("content", ""),
                }
            else:
                entry["chunk_count"] += 1

        rows = []
        for entry in groups.values():
            rows.append(
                {
                    "document_id": entry["document_id"],
                    "chunk_count": str(entry["chunk_count"]),
                    "sample_content": entry["sample_content"],
                },
            )

        cursor_args = getattr(request, "_cursor", None)
        if not cursor_args:
            return types.SimpleNamespace(rows=rows, cursor=None)

        page_size = int(cursor_args[cursor_args.index("COUNT") + 1])
        return self._start_cursor(rows, page_size)

    def _start_cursor(
        self,
        rows: list[dict[str, Any]],
        page_size: int,
    ) -> Any:
        """Return the first cursor page and retain remaining rows."""
        first_page = rows[:page_size]
        remaining = rows[page_size:]
        if not remaining:
            return types.SimpleNamespace(
                rows=first_page,
                cursor=types.SimpleNamespace(cid=0),
            )

        cursor_id = self._next_cursor_id
        self._next_cursor_id += 1
        self._cursor_rows[cursor_id] = (remaining, page_size)
        return types.SimpleNamespace(
            rows=first_page,
            cursor=types.SimpleNamespace(cid=cursor_id),
        )

    def _read_cursor(self, cursor_id: int) -> Any:
        """Return the next cursor page using the original page size."""
        rows, page_size = self._cursor_rows.pop(cursor_id)
        page = rows[:page_size]
        remaining = rows[page_size:]
        if remaining:
            self._cursor_rows[cursor_id] = (remaining, page_size)
            next_cursor_id = cursor_id
        else:
            next_cursor_id = 0
        return types.SimpleNamespace(
            rows=page,
            cursor=types.SimpleNamespace(cid=next_cursor_id),
        )

    # -- helpers ---------------------------------------------------------

    def _matching_keys(self) -> list[str]:
        """Return all document keys belonging to this index."""
        prefix = f"{self._name}:doc:"
        return [k for k in self._client._docs if k.startswith(prefix)]

    @staticmethod
    def _matches_filters(
        doc: dict[str, Any],
        tag_filters: dict[str, str],
    ) -> bool:
        """Check whether a document satisfies all tag filters."""
        for field, expected in tag_filters.items():
            actual = doc.get(field)
            if actual is None:
                return False
            if str(actual) != expected:
                return False
        return True


class _FakeRedisClient:
    """In-memory fake Redis async client.

    Stores all hash documents in ``self._docs`` and lazily creates
    ``_FakeRedisIndex`` instances for each FT index name.
    """

    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}
        self._indexes: dict[str, _FakeRedisIndex] = {}

    async def aclose(self) -> None:
        """No-op close."""

    def ft(self, name: str) -> _FakeRedisIndex:
        """Return (or lazily create) the search index for *name*."""
        if name not in self._indexes:
            self._indexes[name] = _FakeRedisIndex(self, name)
        return self._indexes[name]

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        """Return an async context manager pipeline."""
        return _FakePipeline(self)

    async def unlink(self, *keys: str) -> None:
        """Remove one or more keys from the data store."""
        for key in keys:
            self._docs.pop(key, None)


def _result(
    docs: list[types.SimpleNamespace],
    total: int,
) -> types.SimpleNamespace:
    """Build a search result namespace matching the real FT.SEARCH
    return shape."""
    return types.SimpleNamespace(docs=docs, total=total)


# ------------------------------------------------------------------
# Test cases
# ------------------------------------------------------------------


class RedisStoreTest(IsolatedAsyncioTestCase):
    """The test cases for the RedisStore class."""

    async def asyncSetUp(self) -> None:
        """Create a RedisStore wired to an in-memory fake client."""
        self._fake_client = _FakeRedisClient()
        self._patcher = patch.object(
            RedisStore,
            "get_client",
            return_value=self._fake_client,
        )
        self._patcher.start()
        self._exit_stack = AsyncExitStack()
        self.store = await self._exit_stack.enter_async_context(
            RedisStore(url="redis://fake"),
        )

    async def asyncTearDown(self) -> None:
        """Close the store and stop the patch."""
        await self._exit_stack.aclose()
        self._patcher.stop()

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

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
                            "created_at": AnyString(),
                            "finished_at": None,
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
                            "created_at": AnyString(),
                            "finished_at": None,
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
                            "created_at": AnyString(),
                            "finished_at": None,
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
                            "created_at": AnyString(),
                            "finished_at": None,
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
            metadata: dict[str, Any],
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

        # Force multiple cursor reads without creating a large fixture.
        with patch(
            "agentscope.rag._vdb._redis._AGGREGATE_PAGE_SIZE",
            1,
        ):
            summaries = await self.store.list_documents("kb-1")
        summaries_by_id = {s.document_id: s for s in summaries}

        self.assertEqual(set(summaries_by_id), {"doc-1", "doc-2"})
        self.assertEqual(summaries_by_id["doc-1"].chunk_count, 2)
        self.assertEqual(summaries_by_id["doc-1"].source, "alpha.txt")
        self.assertEqual(
            summaries_by_id["doc-1"].metadata,
            {"filename": "alpha.txt", "media_type": "text/plain"},
        )
        self.assertEqual(summaries_by_id["doc-2"].chunk_count, 1)
        self.assertEqual(summaries_by_id["doc-2"].source, "beta.md")

    async def test_search_metadata_filter(self) -> None:
        """search applies the metadata_filter as a TAG field filter."""
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
        self.assertEqual([r.document_id for r in results], ["doc-1"])

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"kb_scope": "kb-b"},
        )
        self.assertEqual([r.document_id for r in results], ["doc-2"])
