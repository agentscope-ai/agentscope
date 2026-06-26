# -*- coding: utf-8 -*-
"""Unit tests for the QdrantStore class."""
from contextlib import AsyncExitStack
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    QdrantStore,
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


class QdrantStoreTest(IsolatedAsyncioTestCase):
    """The test cases for the QdrantStore class."""

    async def asyncSetUp(self) -> None:
        """Create an in-memory Qdrant store before each test."""
        self._exit_stack = AsyncExitStack()
        self.store = await self._exit_stack.enter_async_context(
            QdrantStore(location=":memory:"),
        )

    async def asyncTearDown(self) -> None:
        """Close the store after each test."""
        await self._exit_stack.aclose()

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
        self.assertEqual([r.document_id for r in results], ["doc-1"])

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"kb_scope": "kb-b"},
        )
        self.assertEqual([r.document_id for r in results], ["doc-2"])
