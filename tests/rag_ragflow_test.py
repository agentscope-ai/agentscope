# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the ``RAGFlowKnowledge`` class.

The RAGFlow SDK client is fully mocked here — none of the SDK is imported
at runtime (AgentScope imports it lazily inside ``get_client()``) — so the
tests run in CI even though ``ragflow-sdk`` is an optional extra.  They
exercise AgentScope's mapping / dedup / threshold logic without needing a
live RAGFlow server.
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


def _dump_result(result: VectorSearchResult) -> dict:
    """Reduce a search hit to its deterministic, semantic fields.

    ``VectorSearchResult``/``Chunk``/``TextBlock`` are pydantic models whose
    ``model_dump()`` carries dynamic ``id`` timestamps, so comparisons use a
    compact projection instead of the raw dump.
    """
    return {
        "score": result.score,
        "document_id": result.document_id,
        "source": result.chunk.source,
        "content": result.chunk.content.text,
        "chunk_index": result.chunk.chunk_index,
        "ragflow_chunk_id": result.chunk.metadata["ragflow_chunk_id"],
    }


def _sdk_chunk(
    content: str,
    document_id: str,
    doc_name: str = "handbook.txt",
    chunk_id: str = "chunk-doc0",
    similarity: float = 0.8,
    dataset_id: str = "kb-1",
) -> MagicMock:
    """Build a stand-in for a ``ragflow_sdk.Chunk``.

    Mirrors the real SDK ``Chunk``: ``similarity``, ``document_id``,
    ``document_name``, ``id``, ``dataset_id`` and ``content`` are always
    initialised.
    """
    chunk = MagicMock()
    chunk.content = content
    chunk.document_name = doc_name
    chunk.similarity = similarity
    chunk.document_id = document_id
    chunk.dataset_id = dataset_id
    chunk.id = chunk_id
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

    @unittest.skipUnless(
        _RAGFLOW_SDK_AVAILABLE,
        "ragflow-sdk is required to instantiate the real client",
    )
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

    async def test_search_returns_hits_across_documents(self) -> None:
        """Search maps SDK chunks to entries carrying their true doc ids."""
        hits = [
            _sdk_chunk(
                "PTO is 4 weeks.",
                document_id="doc-handbook",
                doc_name="handbook.txt",
                chunk_id="chunk-pto",
                similarity=0.9,
            ),
            _sdk_chunk(
                "Sick leave is 10 days.",
                document_id="doc-sick",
                doc_name="sick.txt",
                chunk_id="chunk-sick",
                similarity=0.7,
            ),
        ]
        self.mock_client.retrieve.return_value = hits

        results = await self.kb.search(["What is PTO?"], top_k=5)

        dumped = [_dump_result(result) for result in results]
        self.assertListEqual(
            dumped,
            [
                {
                    "score": 0.9,
                    "document_id": "doc-handbook",
                    "source": "handbook.txt",
                    "content": "PTO is 4 weeks.",
                    "chunk_index": 0,
                    "ragflow_chunk_id": "chunk-pto",
                },
                {
                    "score": 0.7,
                    "document_id": "doc-sick",
                    "source": "sick.txt",
                    "content": "Sick leave is 10 days.",
                    "chunk_index": 0,
                    "ragflow_chunk_id": "chunk-sick",
                },
            ],
        )
        # Results never carry the dataset id *as* the document id.
        self.assertNotIn(self.kb.dataset_id, [r.document_id for r in results])

    async def test_search_dedups_by_chunk_id_within_a_document(self) -> None:
        """The same RAGFlow chunk is not duplicated across queries."""
        q1 = [
            _sdk_chunk(
                "A",
                document_id="doc-x",
                chunk_id="chunk-a",
                similarity=0.6,
            ),
        ]
        q2 = [
            _sdk_chunk(
                "A",
                document_id="doc-x",
                chunk_id="chunk-a",
                similarity=0.8,
            ),
            _sdk_chunk(
                "A different chunk",
                document_id="doc-x",
                chunk_id="chunk-b",
                similarity=0.5,
            ),
        ]
        self.mock_client.retrieve.side_effect = [q1, q2]

        results = await self.kb.search(["q1", "q2"], top_k=5)

        # q1's chunk (score 0.6) is superseded by q2's same chunk (0.8);
        # chunk-b is a distinct chunk, so it is kept too.
        self.assertListEqual(
            [r.document_id for r in results],
            ["doc-x", "doc-x"],
        )
        self.assertEqual(
            [r.chunk.metadata["ragflow_chunk_id"] for r in results],
            ["chunk-a", "chunk-b"],
        )
        self.assertAlmostEqual(results[0].score, 0.8)

    async def test_search_applies_score_threshold(self) -> None:
        """A client-side score_threshold filters weak hits."""
        hits = [
            _sdk_chunk(
                "best",
                document_id="doc-x",
                chunk_id="chunk-best",
                similarity=0.9,
            ),
            _sdk_chunk(
                "weak",
                document_id="doc-x",
                chunk_id="chunk-weak",
                similarity=0.1,
            ),
        ]
        self.mock_client.retrieve.return_value = hits

        results = await self.kb.search(
            ["query"],
            top_k=5,
            score_threshold=0.5,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk.content.text, "best")

    async def test_search_ignores_hits_without_a_document(self) -> None:
        """Chunks RAGFlow cannot attach to a document are dropped."""
        hits = [
            _sdk_chunk(
                "orphan",
                document_id="",
                chunk_id="chunk-orphan",
                similarity=0.9,
            ),
            _sdk_chunk(
                "real",
                document_id="doc-x",
                chunk_id="chunk-real",
                similarity=0.6,
            ),
        ]
        self.mock_client.retrieve.return_value = hits

        results = await self.kb.search(["query"])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, "doc-x")

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

    async def test_insert_document_uploads_and_triggers_parse(self) -> None:
        """insert_document uploads raw bytes and requests async parsing."""
        self.mock_dataset.upload_documents.return_value = [
            _sdk_document("doc-abc", "handbook.pdf"),
        ]

        document_id = await self.kb.insert_document(
            b"%PDF-1.4",
            "handbook.pdf",
        )

        self.assertEqual(document_id, "doc-abc")
        self.mock_dataset.upload_documents.assert_called_once_with(
            [{"display_name": "handbook.pdf", "blob": b"%PDF-1.4"}],
        )
        # RAGFlow indexes asynchronously; the doc id is submitted for parse.
        self.mock_dataset.async_parse_documents.assert_called_once_with(
            ["doc-abc"],
        )

    async def test_delete_document(self) -> None:
        """delete_document forwards the id to the dataset."""
        await self.kb.delete_document("doc-abc")
        self.mock_dataset.delete_documents.assert_called_once_with(
            ids=["doc-abc"],
        )

    async def test_list_documents_maps_to_summaries(self) -> None:
        """list_documents maps SDK documents to DocumentSummary objects."""
        self.mock_dataset.list_documents.return_value = [
            _sdk_document(
                "doc-1",
                "handbook.pdf",
                chunk_count=5,
                progress=80.0,
            ),
            _sdk_document("doc-2", "sick.txt", chunk_count=2, run="DONE"),
        ]

        summaries = await self.kb.list_documents()

        self.assertIsInstance(summaries[0], DocumentSummary)
        self.assertEqual(
            [s.model_dump() for s in summaries],
            [
                {
                    "document_id": "doc-1",
                    "source": "handbook.pdf",
                    "chunk_count": 5,
                    "metadata": {
                        "parse_progress": 80.0,
                        "run": "DONE",  # default run from helper
                        "size": 1024,  # default size from helper
                    },
                },
                {
                    "document_id": "doc-2",
                    "source": "sick.txt",
                    "chunk_count": 2,
                    "metadata": {
                        "parse_progress": 100.0,
                        "run": "DONE",
                        "size": 1024,
                    },
                },
            ],
        )

    async def test_list_chunks_maps_to_agentscope_chunks(self) -> None:
        """list_chunks maps SDK chunk data onto AgentScope chunks."""
        doc = _sdk_document("doc-1", "handbook.txt")
        self.mock_dataset.list_documents.return_value = [doc]
        doc.list_chunks.return_value = [
            _sdk_chunk(
                "c0",
                document_id="doc-1",
                doc_name="handbook.txt",
                chunk_id="ragflow-chunk-0",
            ),
            _sdk_chunk(
                "c1",
                document_id="doc-1",
                doc_name="handbook.txt",
                chunk_id="ragflow-chunk-1",
            ),
        ]

        chunks = await self.kb.list_chunks("doc-1", offset=0, limit=2)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].content.text, "c0")
        self.assertEqual(chunks[1].content.text, "c1")
        self.assertEqual(chunks[0].chunk_index, 0)
        self.assertEqual(chunks[1].chunk_index, 1)
        self.assertEqual(
            chunks[0].metadata["ragflow_chunk_id"],
            "ragflow-chunk-0",
        )


if __name__ == "__main__":
    unittest.main()
