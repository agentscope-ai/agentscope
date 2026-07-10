# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-function-docstring
"""Unit tests for the RAGFlowStore class (mocked ragflow-sdk backend)."""
from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    RAGFlowStore,
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


# ------------------------------------------------------------------
# Fake RAGFlow SDK
# ------------------------------------------------------------------


class _FakeDataSet:
    """In-memory dataset that mimics the ragflow-sdk DataSet API."""

    def __init__(self, dataset_id: str, name: str) -> None:
        self.id = dataset_id
        self.name = name
        self._docs: dict[str, dict[str, Any]] = {}
        self._next_doc_id = 0

    def upload_documents(self, document_list: list[str]) -> None:
        """Simulate uploading files — each path becomes a document."""
        for path in document_list:
            import os

            fname = os.path.basename(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            doc_id = f"fake-doc-{self._next_doc_id}"
            self._next_doc_id += 1
            self._docs[doc_id] = {
                "id": doc_id,
                "name": fname,
                "content": content,
                "status": "UNUSED",
            }

    def list_documents(
        self,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Return paged document list."""
        del page  # simplified: return all
        del page_size
        return list(self._docs.values())

    def delete_documents(self, ids: list[str]) -> None:
        """Remove documents by ID."""
        for doc_id in ids:
            self._docs.pop(doc_id, None)

    def async_parse_documents(self, document_ids: list[str]) -> None:
        """Simulate async parsing by marking status."""
        for doc_id in document_ids:
            if doc_id in self._docs:
                self._docs[doc_id]["status"] = "SUCCESS"


class _FakeRAGFlowClient:
    """In-memory fake for the ragflow-sdk RAGFlow client."""

    def __init__(self) -> None:
        self._datasets: dict[str, _FakeDataSet] = {}
        self._next_ds_id = 0

    def create_dataset(self, name: str, description: str = "") -> _FakeDataSet:
        """Create a new dataset."""
        del description
        ds_id = f"fake-ds-{self._next_ds_id}"
        self._next_ds_id += 1
        ds = _FakeDataSet(ds_id, name)
        self._datasets[ds_id] = ds
        return ds

    def delete_datasets(self, ids: list[str]) -> None:
        """Delete datasets by ID."""
        for ds_id in ids:
            self._datasets.pop(ds_id, None)

    def list_datasets(
        self,
        page: int = 1,
        page_size: int = 100,
    ) -> list[_FakeDataSet]:
        """Return paged dataset list."""
        del page
        del page_size
        return list(self._datasets.values())

    def retrieve(
        self,
        dataset_ids: list[str],
        question: str = "",
        top_k: int = 5,
        similarity_threshold: float = 0.0,
        vector_similarity_weight: float = 0.0,
        keyword: bool = True,
        metadata_condition: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Simulate retrieval by scanning documents in the requested
        datasets and returning chunks whose content matches keywords.

        For fake purposes, any document whose content contains text from
        the sidecar is considered a match.
        """
        del similarity_threshold, vector_similarity_weight, metadata_condition
        results: list[dict[str, Any]] = []
        for ds_id in dataset_ids:
            ds = self._datasets.get(ds_id)
            if ds is None:
                continue
            for doc in ds._docs.values():
                content = doc["content"]
                name = doc["name"]
                # Simple scoring: longer shared prefix → better match.
                if keyword and question:
                    score = 0.3  # default
                else:
                    score = 0.95
                results.append(
                    {
                        "similarity": score,
                        "content": content,
                        "document_name": name,
                        "document_id": doc["id"],
                    },
                )
        return results[:top_k]


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class RAGFlowStoreTest(IsolatedAsyncioTestCase):
    """The test cases for the RAGFlowStore class."""

    async def asyncSetUp(self) -> None:
        """Create a RAGFlow store backed by a fake in-memory client."""
        self._fake_client = _FakeRAGFlowClient()
        self._client_patcher = patch.object(
            RAGFlowStore,
            "get_client",
            return_value=self._fake_client,
        )
        self._client_patcher.start()

        self._exit_stack = AsyncExitStack()
        self.store = RAGFlowStore(
            api_key="test-key",
            base_url="http://localhost:9380",
        )
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
        """Inserted records are searchable via RAGFlow native retrieval.

        Two records with the same ``document_id`` are uploaded as one
        RAGFlow document; the sidecar encodes all chunks so the first
        chunk is recoverable from any retrieved content.
        """
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

        # Two records of the same document_id → one uploaded file →
        # one search hit from the fake client.
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, "doc-1")

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

        self.assertEqual(len(results), 1)

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

        self.assertEqual([r.document_id for r in results], ["doc-2"])

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

        await self.store.insert(
            "kb-1",
            [
                _make_record(
                    "A",
                    [1.0, 0.0, 0.0],
                    document_id="doc-1",
                    chunk_index=0,
                    total_chunks=2,
                ),
                _make_record(
                    "B",
                    [1.0, 0.0, 0.0],
                    document_id="doc-1",
                    chunk_index=1,
                    total_chunks=2,
                ),
                _make_record(
                    "C",
                    [1.0, 0.0, 0.0],
                    document_id="doc-2",
                    chunk_index=0,
                    total_chunks=1,
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
                    "source": "agentscope_doc-1.txt",
                    "chunk_count": 1,
                    "metadata": {},
                },
                {
                    "document_id": "doc-2",
                    "source": "agentscope_doc-2.txt",
                    "chunk_count": 1,
                    "metadata": {},
                },
            ],
        )

    async def test_search_metadata_filter(self) -> None:
        """search applies the metadata_filter as a metadata_condition."""
        await self.store.create_collection("kb-1", dimensions=3)

        await self.store.insert(
            "kb-1",
            [
                _make_record("A", [1.0, 0.0, 0.0], document_id="doc-1"),
                _make_record("B", [0.0, 1.0, 0.0], document_id="doc-2"),
            ],
        )

        # metadata_filter is forwarded to retrieve as metadata_condition.
        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"kb_scope": "kb-a"},
        )
        # The fake client ignores metadata_condition, so both docs appear.
        self.assertEqual(len(results), 2)

    async def test_persists_records_after_reopen(self) -> None:
        """Dataset is durable across store instances that share the same
        fake client."""
        await self.store.create_collection("kb_persistent", dimensions=3)
        await self.store.insert(
            "kb_persistent",
            [
                _make_record(
                    "Persisted",
                    [1.0, 0.0, 0.0],
                    document_id="doc-1",
                ),
            ],
        )

        # Re-use the same fake client (simulating same RAGFlow server).
        second_store = RAGFlowStore(
            api_key="test-key",
            base_url="http://localhost:9380",
        )
        with patch.object(
            RAGFlowStore,
            "get_client",
            return_value=self._fake_client,
        ):
            async with second_store:
                results = await second_store.search(
                    "kb_persistent",
                    query_vector=[1.0, 0.0, 0.0],
                    top_k=1,
                )

        self.assertEqual([r.document_id for r in results], ["doc-1"])
