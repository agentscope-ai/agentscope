# -*- coding: utf-8 -*-
"""Unit tests for the MilvusLiteStore class."""
import tempfile
import unittest
from contextlib import AsyncExitStack
from importlib.util import find_spec
from pathlib import Path
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    MilvusLiteStore,
    VectorRecord,
    VectorSearchResult,
)


_MILVUS_LITE_AVAILABLE = (
    find_spec("pymilvus") is not None and find_spec("milvus_lite") is not None
)


def _dump_results(results: list[VectorSearchResult]) -> list[dict]:
    """Convert search results into plain dicts for comparison."""
    return [result.model_dump() for result in results]


def _make_record(
    text: str,
    vector: list[float],
    document_id: str,
    chunk_index: int = 0,
    total_chunks: int = 1,
) -> VectorRecord:
    """Build a VectorRecord for testing."""
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


@unittest.skipUnless(
    _MILVUS_LITE_AVAILABLE,
    "pymilvus and milvus_lite are required for MilvusLiteStore tests",
)
class MilvusLiteStoreTest(IsolatedAsyncioTestCase):
    """The test cases for the MilvusLiteStore class."""

    async def asyncSetUp(self) -> None:
        """Create a temporary Milvus Lite store before each test."""
        self._exit_stack = AsyncExitStack()
        tmpdir = self._exit_stack.enter_context(tempfile.TemporaryDirectory())
        self._tmpdir = Path(tmpdir)
        db_path = self._tmpdir / "test.db"
        self.store = await self._exit_stack.enter_async_context(
            MilvusLiteStore(uri=str(db_path)),
        )

    async def asyncTearDown(self) -> None:
        """Close the store and remove the temporary database."""
        await self._exit_stack.aclose()

    async def test_collection_lifecycle(self) -> None:
        """Collections can be created, checked, and deleted."""
        self.assertEqual(await self.store.has_collection("kb_1"), False)

        await self.store.create_collection("kb_1", dimensions=3)
        self.assertEqual(await self.store.has_collection("kb_1"), True)

        # Creating an existing collection is a no-op
        await self.store.create_collection("kb_1", dimensions=3)
        self.assertEqual(await self.store.has_collection("kb_1"), True)

        await self.store.delete_collection("kb_1")
        self.assertEqual(await self.store.has_collection("kb_1"), False)

    async def test_insert_and_search(self) -> None:
        """Inserted records are searchable, ordered by similarity."""
        await self.store.create_collection("kb_1", dimensions=3)
        await self.store.insert(
            "kb_1",
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
            "kb_1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=2,
        )

        self.assertAlmostEqual(results[0].score, 1.0)
        self.assertAlmostEqual(results[1].score, 0.0)
        dumped = _dump_results(results)
        for result in dumped:
            result["score"] = 0.0
        self.assertEqual(
            dumped,
            [
                {
                    "score": 0.0,
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
        await self.store.create_collection("kb_1", dimensions=3)
        await self.store.insert(
            "kb_1",
            [
                _make_record("A", [1.0, 0.0, 0.0], document_id="doc-1"),
                _make_record("B", [0.9, 0.1, 0.0], document_id="doc-2"),
                _make_record("C", [0.0, 0.0, 1.0], document_id="doc-3"),
            ],
        )

        results = await self.store.search(
            "kb_1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=1,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, "doc-1")

    async def test_delete_by_document_id(self) -> None:
        """delete removes all records of one document only."""
        await self.store.create_collection("kb_1", dimensions=3)
        await self.store.insert(
            "kb_1",
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

        await self.store.delete("kb_1", document_id="doc-1")

        results = await self.store.search(
            "kb_1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
        )

        self.assertEqual([r.document_id for r in results], ["doc-2"])

    async def test_insert_empty_records(self) -> None:
        """Inserting an empty record list is a no-op."""
        await self.store.create_collection("kb_1", dimensions=3)
        await self.store.insert("kb_1", [])

        results = await self.store.search(
            "kb_1",
            query_vector=[1.0, 0.0, 0.0],
        )

        self.assertEqual(_dump_results(results), [])

    async def test_list_documents_aggregates_by_document_id(self) -> None:
        """list_documents groups chunks by document_id."""
        await self.store.create_collection("kb_1", dimensions=3)

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
            "kb_1",
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

        summaries = await self.store.list_documents("kb_1")
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
        """search applies the metadata_filter as a payload predicate."""
        await self.store.create_collection("kb_1", dimensions=3)

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
            "kb_1",
            [
                _record("A", "doc-1", "kb-a"),
                _record("B", "doc-2", "kb-b"),
            ],
        )

        results = await self.store.search(
            "kb_1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"kb_scope": "kb-a"},
        )
        self.assertEqual([r.document_id for r in results], ["doc-1"])

        summaries = await self.store.list_documents(
            "kb_1",
            metadata_filter={"kb_scope": "kb-b"},
        )
        self.assertEqual([s.document_id for s in summaries], ["doc-2"])

    async def test_persists_records_after_reopen(self) -> None:
        """Records remain available after closing and reopening the DB."""
        db_path = self._tmpdir / "persistent.db"

        async with MilvusLiteStore(uri=str(db_path)) as first_store:
            await first_store.create_collection("kb_persistent", dimensions=3)
            await first_store.insert(
                "kb_persistent",
                [
                    _make_record(
                        "Persisted",
                        [1.0, 0.0, 0.0],
                        document_id="doc-1",
                    ),
                ],
            )

        async with MilvusLiteStore(uri=str(db_path)) as second_store:
            await second_store.create_collection("kb_persistent", dimensions=3)
            results = await second_store.search(
                "kb_persistent",
                query_vector=[1.0, 0.0, 0.0],
                top_k=1,
            )

        self.assertEqual([r.document_id for r in results], ["doc-1"])
