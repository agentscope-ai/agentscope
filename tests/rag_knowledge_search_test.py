# -*- coding: utf-8 -*-
"""Tests that distance-based vector metrics rank correctly.

Distance metrics (Qdrant ``Euclid`` / ``Manhattan``, Milvus ``L2``) are
lower-is-better, while similarity metrics (cosine / dot-product) are
higher-is-better.  To keep ``VectorSearchResult.score`` meaning
"higher is more relevant" everywhere, the backends normalize distance
scores (negating them) so the shared ``KnowledgeBase.search`` post
processing can rank results a single way.
"""
from __future__ import annotations

import unittest

from agentscope.embedding import EmbeddingResponse
from agentscope.message import TextBlock
from agentscope.rag import KnowledgeBase, QdrantStore, VectorRecord
from agentscope.rag._document import Chunk


class _StubEmbedding:
    """A stub embedding model returning the query vector unchanged."""

    supports_multimodal = False
    dimensions = 3

    async def __call__(self, inputs: list) -> EmbeddingResponse:
        return EmbeddingResponse(embeddings=[[0.0, 0.0, 0.0]] * len(inputs))


def _record(text: str, vector: list[float], document_id: str) -> VectorRecord:
    """Build a :class:`VectorRecord` for testing."""
    return VectorRecord(
        vector=vector,
        document_id=document_id,
        chunk=Chunk(
            content=TextBlock(text=text),
            source=f"{document_id}.txt",
            chunk_index=0,
            total_chunks=1,
        ),
    )


class DistanceMetricRankingTests(unittest.IsolatedAsyncioTestCase):
    """Tests for distance-metric ranking across the store and the KB."""

    async def test_qdrant_euclid_normalizes_scores(self) -> None:
        """A Qdrant ``Euclid`` store returns higher-is-better scores.

        The raw L2 distance is negated so that a lower distance (a
        nearer match) yields a higher ``score``.
        """
        store = QdrantStore(location=":memory:", distance="Euclid")
        async with store:
            await store.create_collection("kb-1", dimensions=3)
            await store.insert(
                "kb-1",
                [
                    _record("near", [1.0, 0.0, 0.0], "doc-1"),
                    _record("far", [10.0, 0.0, 0.0], "doc-2"),
                    _record("closest", [0.0, 0.0, 0.0], "doc-3"),
                ],
            )

            results = await store.search("kb-1", [0.0, 0.0, 0.0], top_k=3)

            # Negated distances: 0.0, -1.0, -10.0 (higher = nearer).
            self.assertEqual(
                [r.document_id for r in results],
                ["doc-3", "doc-1", "doc-2"],
            )
            self.assertGreater(results[0].score, results[1].score)
            self.assertGreater(results[1].score, results[2].score)

    async def test_knowledge_search_ranks_nearest_first(self) -> None:
        """``KnowledgeBase.search`` returns the nearest match first for a
        distance metric, no longer reversing the ranking."""
        store = QdrantStore(location=":memory:", distance="Euclid")
        async with store:
            await store.create_collection("kb-1", dimensions=3)
            await store.insert(
                "kb-1",
                [
                    _record("far", [10.0, 0.0, 0.0], "doc-2"),
                    _record("closest", [0.0, 0.0, 0.0], "doc-3"),
                    _record("near", [1.0, 0.0, 0.0], "doc-1"),
                ],
            )

            kb = KnowledgeBase(
                name="test-kb",
                description="Test knowledge base.",
                embedding_model=_StubEmbedding(),
                vector_store=store,
                collection="kb-1",
            )
            results = await kb.search(["query"], top_k=3)

            self.assertEqual(
                [r.document_id for r in results],
                ["doc-3", "doc-1", "doc-2"],
            )


if __name__ == "__main__":
    unittest.main()
