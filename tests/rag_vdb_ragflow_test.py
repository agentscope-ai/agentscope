# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-function-docstring
"""Unit tests for the RAGFlowStore class (mocked ragflow-sdk backend)."""
from __future__ import annotations

import base64
import json
import os
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
        """Simulate uploading files — each path becomes a document.
        The document name preserves the original filename so delete-by-name
        and list-by-document_id work correctly."""
        for path in document_list:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            doc_id = f"fake-doc-{self._next_doc_id}"
            self._next_doc_id += 1
            self._docs[doc_id] = {
                "id": doc_id,
                "name": os.path.basename(path),
                "content": content,
                "status": "UNUSED",
            }

    def list_documents(
        self,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Return paged document list.  Returns all for simplicity."""
        _ = page, page_size
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
        _ = description
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
        _ = page, page_size
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
        """Simulate retrieval by returning all documents in the dataset
        with a default score."""
        _, _, _, _ = (
            question,
            similarity_threshold,
            vector_similarity_weight,
            metadata_condition,
        )
        results: list[dict[str, Any]] = []
        for ds_id in dataset_ids:
            ds = self._datasets.get(ds_id)
            if ds is None:
                continue
            for doc in ds._docs.values():
                if keyword and question:
                    _ = question
                results.append(
                    {
                        "similarity": 0.95,
                        "content": doc["content"],
                        "document_name": doc["name"],
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

    # -- helper: upload a record and return its sidecar dict ----------
    @staticmethod
    def _sidecar_from_record(rec: VectorRecord) -> dict[str, Any]:
        sidecar_str = RAGFlowStore._build_sidecar(rec)
        return json.loads(sidecar_str)

    # ----------------------------------------------------------------

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

        Each chunk is uploaded as a separate file, so two records produce
        two search hits.
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

        # Each chunk is a separate fake document → 2 results.
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result.document_id, "doc-1")

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
        # Two unique document_ids.
        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[0].document_id, "doc-1")
        self.assertEqual(summaries[0].chunk_count, 2)
        self.assertEqual(summaries[0].source, "doc-1.txt")
        self.assertEqual(summaries[1].document_id, "doc-2")
        self.assertEqual(summaries[1].chunk_count, 1)

    async def test_search_metadata_filter(self) -> None:
        """search applies metadata_filter client-side."""
        await self.store.create_collection("kb-1", dimensions=3)

        rec_a = VectorRecord(
            vector=[1.0, 0.0, 0.0],
            document_id="doc-1",
            chunk=Chunk(
                content=TextBlock(text="A"),
                source="doc-1.txt",
                chunk_index=0,
                total_chunks=1,
                metadata={"kb_scope": "kb-a"},
            ),
        )
        rec_b = VectorRecord(
            vector=[1.0, 0.0, 0.0],
            document_id="doc-2",
            chunk=Chunk(
                content=TextBlock(text="B"),
                source="doc-2.txt",
                chunk_index=0,
                total_chunks=1,
                metadata={"kb_scope": "kb-b"},
            ),
        )

        await self.store.insert("kb-1", [rec_a, rec_b])

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"kb_scope": "kb-a"},
        )
        self.assertEqual([r.document_id for r in results], ["doc-1"])

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

    async def test_list_documents_metadata_filter(self) -> None:
        """list_documents applies metadata_filter client-side."""
        await self.store.create_collection("kb-1", dimensions=3)

        rec_a = VectorRecord(
            vector=[1.0, 0.0, 0.0],
            document_id="doc-1",
            chunk=Chunk(
                content=TextBlock(text="A"),
                source="doc-1.txt",
                chunk_index=0,
                total_chunks=1,
                metadata={"scope": "public"},
            ),
        )
        rec_b = VectorRecord(
            vector=[1.0, 0.0, 0.0],
            document_id="doc-2",
            chunk=Chunk(
                content=TextBlock(text="B"),
                source="doc-2.txt",
                chunk_index=0,
                total_chunks=1,
                metadata={"scope": "private"},
            ),
        )

        await self.store.insert("kb-1", [rec_a, rec_b])

        summaries = await self.store.list_documents(
            "kb-1",
            metadata_filter={"scope": "public"},
        )
        self.assertEqual([s.document_id for s in summaries], ["doc-1"])


class EncodingTest(IsolatedAsyncioTestCase):
    """Tests for filename encoding / decoding and sidecar helpers."""

    def test_make_and_parse_filename_roundtrips(self) -> None:
        """Filename should roundtrip document_id correctly via Base64."""
        for doc_id in ["my-document_123", "a/b:c\\d", "doc.1", "doc-1"]:
            fname = RAGFlowStore._make_filename(doc_id)
            self.assertTrue(fname.startswith("agentscope_"))
            self.assertTrue(fname.endswith(".txt"))
            parsed = RAGFlowStore._parse_document_id_from_name(fname)
            self.assertEqual(
                parsed,
                doc_id,
                f"Failed roundtrip for {doc_id!r}: got {parsed!r}",
            )

    def test_make_filename_is_unique(self) -> None:
        """Different document_ids produce different filenames."""
        f1 = RAGFlowStore._make_filename("doc-1")
        f2 = RAGFlowStore._make_filename("doc/1")
        f3 = RAGFlowStore._make_filename("doc.1")
        self.assertNotEqual(f1, f2)
        self.assertNotEqual(f1, f3)
        self.assertNotEqual(f2, f3)

    def test_parse_document_id_invalid_returns_none(self) -> None:
        """_parse_document_id_from_name returns None for non-matching names."""
        self.assertIsNone(
            RAGFlowStore._parse_document_id_from_name("not-matching.txt"),
        )
        encoded = base64.urlsafe_b64encode(b"test").decode("ascii")
        self.assertIsNotNone(
            RAGFlowStore._parse_document_id_from_name(
                f"agentscope_{encoded}.txt",
            ),
        )

    def test_build_sidecar_single_record(self) -> None:
        """_build_sidecar returns compact JSON with all required fields."""
        rec = _make_record(
            "hello",
            [1.0, 0.0],
            "doc-x",
            chunk_index=2,
            total_chunks=5,
        )
        sidecar_str = RAGFlowStore._build_sidecar(rec)
        data = json.loads(sidecar_str)
        self.assertEqual(data["document_id"], "doc-x")
        self.assertEqual(data["chunk_index"], 2)
        self.assertEqual(data["total_chunks"], 5)
        self.assertEqual(data["source"], "doc-x.txt")

    def test_parse_sidecar_from_content(self) -> None:
        """_parse_sidecar extracts JSON from a '# agentscope: ' line."""
        rec = _make_record("hi", [1.0], "d1")
        sidecar_str = RAGFlowStore._build_sidecar(rec)
        content = f"# agentscope: {sidecar_str}\nsome text content"
        parsed = RAGFlowStore._parse_sidecar(content)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["document_id"], "d1")  # type: ignore[index]

    def test_parse_sidecar_no_match(self) -> None:
        """_parse_sidecar returns None when no sidecar line exists."""
        self.assertIsNone(RAGFlowStore._parse_sidecar("plain text"))

    def test_chunk_from_sidecar_reconstructs(self) -> None:
        """_chunk_from_sidecar rebuilds a Chunk from embedded data."""
        rec = _make_record(
            "reconstruct me",
            [1.0],
            "doc-x",
            chunk_index=3,
            total_chunks=4,
        )
        sidecar = RAGFlowStore._build_sidecar(rec)
        content = f"# agentscope: {sidecar}\ntext"
        recovered = RAGFlowStore._chunk_from_sidecar(content)
        self.assertIsNotNone(recovered)
        self.assertEqual(
            recovered.content.text,
            "reconstruct me",
        )  # type: ignore[union-attr]
        self.assertEqual(recovered.chunk_index, 3)  # type: ignore[union-attr]
        self.assertEqual(
            recovered.metadata["document_id"],
            "doc-x",
        )  # type: ignore[union-attr, index]

    def test_matches_metadata_filter(self) -> None:
        """_matches_metadata_filter applies flat key-value predicates."""
        sidecar = {
            "document_id": "d1",
            "chunk": {
                "metadata": {"scope": "public", "org": "a"},
            },
        }
        self.assertTrue(
            RAGFlowStore._matches_metadata_filter(sidecar, None),
        )
        self.assertTrue(
            RAGFlowStore._matches_metadata_filter(
                sidecar,
                {"scope": "public"},
            ),
        )
        self.assertFalse(
            RAGFlowStore._matches_metadata_filter(
                sidecar,
                {"scope": "private"},
            ),
        )
        self.assertFalse(
            RAGFlowStore._matches_metadata_filter(None, {"scope": "public"}),
        )
