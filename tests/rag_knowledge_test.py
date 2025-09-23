# -*- coding: utf-8 -*-
"""Test the RAG knowledge implementations."""
import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.embedding import DashScopeTextEmbedding
from agentscope.message import TextBlock
from agentscope.rag import (
    SimpleKnowledge,
    QdrantStore,
    Document,
    DocMetadata,
)


class RAGKnowledgeTest(IsolatedAsyncioTestCase):
    """Test cases for RAG knowledge implementations."""

    async def test_simple_knowledge(self) -> None:
        """Test the SimpleKnowledge implementation."""

        knowledge = SimpleKnowledge(
            embedding_model=DashScopeTextEmbedding(
                model_name="text-embedding-v4",
                api_key=os.environ["DASHSCOPE_API_KEY"],
            ),
            embedding_store=QdrantStore(
                location=":memory:",
                collection_name="test",
                dimensions=1024,
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
            score_threshold=0.5,
        )

        self.assertEqual(len(res), 1)
        self.assertEqual(
            res[0].metadata.content["text"],
            "This is an apple.",
        )
        self.assertEqual(
            res[0].score,
            0.6267428997503158,
        )
