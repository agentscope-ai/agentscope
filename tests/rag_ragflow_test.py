# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the RAGFlowKnowledge class.

The ``ragflow-sdk`` package is an optional dependency, so these tests
skip cleanly when it is not installed (matching the pattern used by the
other vector-store backend tests).  When it *is* installed, the SDK
client is mocked so the tests exercise AgentScope's mapping / dedup /
threshold logic without needing a live RAGFlow server.
"""
import unittest
from importlib.util import find_spec
from unittest.mock import MagicMock

from agentscope.rag import (
    DocumentSummary,
    RAGFlowConfig,
    RAGFlowKnowledge,
    VectorSearchResult,
)


_RAGFLOW_SDK_AVAILABLE = find_spec("ragflow_sdk") is not None


def _sdk_chunk(
    content: str,
    index: int,
    doc_name: str = "handbook.txt",
    chunk_id: str | None = None,
    similarity: float = 0.8,
    dataset_id: str = "kb-1",
) -> MagicMock:
    """Build a stand-in for a ``ragflow_sdk.Chunk``."""
    chunk = MagicMock()
    chunk.content = content
    chunk.document_name = doc_name
    chunk.similarity = similarity
    chunk.dataset_id = dataset_id
    chunk.id = chunk_id or f"chunk-{index}"
    return chunk


def _sdk_document(
    document_id: str,
    name: str,
    chunk_count: int = 3,
    progress: float = 100.0,
    run: str = "DONE",
) -> MagicMock:
    """Build a stand-in for a ``ragflow_sdk.Document``."""
    doc = MagicMock()
    doc.id = document_id
    doc.name = name
    doc.chunk_count = chunk_count
    doc.progress = progress
    doc.run = run
    doc.size = 1024
    return doc


@unittest.skipUnless(
    _RAGFLOW_SDK_AVAILABLE,
    "ragflow-sdk is required for RAGFlowKnowledge tests",
)
class RAGFlowKnowledgeTest(unittest.IsolatedAsyncioTestCase):
    """The test cases for the RAGFlowKnowledge class."""

    def setUp(self) -> None:
        """Build a RAGFlowKnowledge wired to a mocked RAGFlow client."""
        self.config = RAGFlowConfig(
            api_key="ragflow-test-key",
            base_url="http://localhost:9380",
            dataset_id="kb-1",
            top_k=10,
            similarity_threshold=0.2,
        )
        self.kb = RAGFlowKnowledge(
            name="company-handbook",
            description="Internal HR docs.",
            config=self.config,
        )

        # Mock the SDK client and its dataset lookup.
        self.mock_client = MagicMock()
        self.mock_dataset = MagicMock()
        self.mock_dataset.id = "kb-1"
        self.kb._client = self.mock_client
        self.mock_client.list_datasets.return_value = [self.mock_dataset]

    # ------------------------------------------------------------------
    # Construction / accessors
    # ------------------------------------------------------------------

    def test_accessors(self) -> None:
        """Read-only accessors surface the bound config."""
        self.assertEqual(self.kb.name, "company-handbook")
        self.assertEqual(self.kb.description, "Internal HR docs.")
        self.assertEqual(self.kb.dataset_id, "kb-1")
        self.assertEqual(self.kb.api_key, "ragflow-test-key")
        self.assertEqual(self.kb.base_url, "http://localhost:9380")
        self.assertEqual(self.kb.config, self.config)

    def test_client_is_lazily_created(self) -> None:
        """The SDK client is created only on first use."""
        self.kb._client = None
        self.kb.get_client()
        self.assertIsNotNone(self.kb.get_client())
        # The same cached instance is reused.
        self.assertIs(self.kb.get_client(), self.kb._client)

    async def test_missing_dataset_raises(self) -> None:
        """A doc op against a missing dataset raises RuntimeError."""
        self.mock_client.list_datasets.return_value = []
        with self.assertRaises(RuntimeError):
            await self.kb.list_documents()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def test_search_returns_vectorsresults(self) -> None:
        """Search maps SDK chunks to VectorSearchResult entries."""
        hits = [
            _sdk_chunk(
                "PTO is 4 weeks.",
                0,
                doc_name="handbook.txt",
                similarity=0.9,
            ),
            _sdk_chunk(
                "Sick leave is 10 days.",
                1,
                doc_name="sick.txt",
                similarity=0.7,
            ),
        ]
        self.mock_client.retrieve.return_value = hits

        results = await self.kb.search(["What is PTO?"], top_k=5)

        self.assertEqual(len(results), 2)
        self.assertIsInstance(results[0], VectorSearchResult)
        # Ordered by descending similarity.
        self.assertAlmostEqual(results[0].score, 0.9)
        self.assertAlmostEqual(results[1].score, 0.7)
        self.assertEqual(results[0].document_id, "kb-1")
        self.assertEqual(results[0].chunk.content.text, "PTO is 4 weeks.")
        self.assertEqual(
            results[0].chunk.metadata["ragflow_chunk_id"],
            "chunk-0",
        )

    async def test_search_dedups_by_document_and_chunk(self) -> None:
        """Cross-query hits dedup by (document_id, chunk_index)."""
        q1 = [_sdk_chunk("A", 1, doc_name="handbook.txt", similarity=0.6)]
        q2 = [_sdk_chunk("A", 1, doc_name="handbook.txt", similarity=0.8)]
        self.mock_client.retrieve.side_effect = [q1, q2]

        results = await self.kb.search(["q1", "q2"], top_k=5)

        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].score, 0.8)

    async def test_search_applies_score_threshold(self) -> None:
        """A client-side score_threshold filters weak hits."""
        hits = [
            _sdk_chunk("best", 0, similarity=0.9),
            _sdk_chunk("weak", 1, similarity=0.1),
        ]
        self.mock_client.retrieve.return_value = hits

        results = await self.kb.search(
            ["query"],
            top_k=5,
            score_threshold=0.5,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk.content.text, "best")

    async def test_search_empty_queries(self) -> None:
        """No queries returns an empty list without calling RAGFlow."""
        results = await self.kb.search([])
        self.assertEqual(results, [])
        self.mock_client.retrieve.assert_not_called()

    async def test_search_passes_retrieval_tuning(self) -> None:
        """Retrieval parameters are forwarded to the SDK retrieve call."""
        config = RAGFlowConfig(
            api_key="k",
            base_url="http://localhost:9380",
            dataset_id="kb-1",
            top_k=42,
            similarity_threshold=0.3,
            vector_similarity_weight=0.5,
            enable_rerank=True,
            rerank_id="rerank-1",
            keyword=True,
        )
        kb = RAGFlowKnowledge(
            name="n",
            description="d",
            config=config,
        )
        kb._client = self.mock_client

        await kb.search(["hello"], top_k=5)

        self.mock_client.retrieve.assert_called_once()
        kwargs = self.mock_client.retrieve.call_args.kwargs
        self.assertEqual(kwargs["dataset_ids"], ["kb-1"])
        self.assertEqual(kwargs["top_k"], 42)
        self.assertEqual(kwargs["similarity_threshold"], 0.3)
        self.assertEqual(kwargs["vector_similarity_weight"], 0.5)
        self.assertEqual(kwargs["rerank_id"], "rerank-1")
        self.assertEqual(kwargs["keyword"], True)

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def test_insert_document_uploads_and_returns_id(self) -> None:
        """insert_document uploads raw bytes and returns the document id."""
        created = _sdk_document("doc-abc", "handbook.pdf")
        self.mock_dataset.upload_documents.return_value = [created]

        document_id = await self.kb.insert_document(
            b"%PDF-1.4",
            "handbook.pdf",
        )

        self.assertEqual(document_id, "doc-abc")
        self.mock_dataset.upload_documents.assert_called_once()
        payload = self.mock_dataset.upload_documents.call_args.args[0][0]
        self.assertEqual(payload["display_name"], "handbook.pdf")
        self.assertEqual(payload["blob"], b"%PDF-1.4")

    async def test_delete_document(self) -> None:
        """delete_document forwards the id to the dataset."""
        await self.kb.delete_document("doc-abc")
        self.mock_dataset.delete_documents.assert_called_once_with(
            ids=["doc-abc"],
        )

    async def test_list_documents_maps_to_summaries(self) -> None:
        """list_documents maps SDK documents to DocumentSummary objects."""
        self.mock_dataset.list_documents.return_value = [
            _sdk_document("doc-1", "handbook.pdf", chunk_count=5),
            _sdk_document("doc-2", "sick.txt", chunk_count=2),
        ]

        summaries = await self.kb.list_documents()

        self.assertEqual(len(summaries), 2)
        self.assertIsInstance(summaries[0], DocumentSummary)
        self.assertEqual(summaries[0].document_id, "doc-1")
        self.assertEqual(summaries[0].source, "handbook.pdf")
        self.assertEqual(summaries[0].chunk_count, 5)
        self.assertEqual(summaries[1].document_id, "doc-2")

    async def test_list_chunks_maps_to_agentscope_chunks(self) -> None:
        """list_chunks maps SDK chunk paging onto AgentScope chunks."""
        doc = _sdk_document("doc-1", "handbook.txt")
        self.mock_dataset.list_documents.return_value = [doc]
        doc.list_chunks.return_value = [
            _sdk_chunk("c0", 0, doc_name="handbook.txt"),
            _sdk_chunk("c1", 1, doc_name="handbook.txt"),
        ]

        chunks = await self.kb.list_chunks("doc-1", offset=0, limit=2)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].content.text, "c0")
        self.assertEqual(chunks[1].content.text, "c1")
        # Rebased to a 0-based chunk index.
        self.assertEqual(chunks[0].chunk_index, 0)
        self.assertEqual(chunks[1].chunk_index, 1)


if __name__ == "__main__":
    unittest.main()
