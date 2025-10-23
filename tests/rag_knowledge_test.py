# -*- coding: utf-8 -*-
"""Test the RAG knowledge implementations."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.embedding import (
    EmbeddingModelBase,
    EmbeddingResponse,
)
from agentscope.message import TextBlock
from agentscope.rag import (
    SimpleKnowledge,
    QdrantStore,
    Document,
    DocMetadata,
)


class TestTextEmbedding(EmbeddingModelBase):
    """A mock text embedding model for testing."""

    supported_modalities: list[str] = ["text"]
    """This class only supports text input."""

    def __init__(self) -> None:
        """The constructor for the mock text embedding model."""
        super().__init__(model_name="mock-model", dimensions=3)

    async def __call__(
        self,
        text: list[TextBlock | str],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Return a fixed embedding for testing."""
        embeddings = []
        for t in text:
            if isinstance(t, dict):
                t = t.get("text")
            if t == "This is an apple":
                embeddings.append([0.1, 0.2, 0.3])
            elif t == "This is a banana":
                embeddings.append([0.9, 0.1, 0.4])
            elif t == "apple":
                embeddings.append([0.15, 0.25, 0.35])

        return EmbeddingResponse(
            embeddings=embeddings,
        )


class RAGKnowledgeTest(IsolatedAsyncioTestCase):
    """Test cases for RAG knowledge implementations."""

    async def test_simple_knowledge(self) -> None:
        """Test the SimpleKnowledge implementation."""

        knowledge = SimpleKnowledge(
            embedding_model=TestTextEmbedding(),
            embedding_store=QdrantStore(
                location=":memory:",
                collection_name="test",
                dimensions=3,
            ),
        )

        await knowledge.add_documents(
            [
                Document(
                    embedding=[0.1, 0.2, 0.3],
                    metadata=DocMetadata(
                        content=TextBlock(
                            type="text",
                            text="This is an apple.",
                        ),
                        doc_id="doc1",
                        chunk_id=1,
                        total_chunks=2,
                    ),
                ),
                Document(
                    embedding=[0.9, 0.1, 0.4],
                    metadata=DocMetadata(
                        content=TextBlock(
                            type="text",
                            text="This is a banana.",
                        ),
                        doc_id="doc1",
                        chunk_id=2,
                        total_chunks=2,
                    ),
                ),
            ],
        )

        res = await knowledge.retrieve(
            query="apple",
            limit=3,
            score_threshold=0.7,
        )

        self.assertEqual(len(res), 1)
        self.assertEqual(
            res[0].metadata.content["text"],
            "This is an apple.",
        )
        self.assertEqual(
            res[0].score,
            0.9974149072579597,
        )
