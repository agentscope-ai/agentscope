# -*- coding: utf-8 -*-
"""Test the GraphKnowledgeBase implementation with real Neo4j."""
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

import pytest

from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.message import TextBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.rag import (
    Document,
    DocMetadata,
    GraphKnowledgeBase,
    Neo4jGraphStore,
)

# Add src to path if needed for development
src_path = Path(__file__).parent.parent.parent / "src"
if src_path.exists() and str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Configuration for Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# Check if Neo4j is available (for skipping tests if not)
NEO4J_AVAILABLE = os.getenv("NEO4J_AVAILABLE", "false").lower() == "true"


class MockTextEmbedding(EmbeddingModelBase):
    """Mock embedding model for testing (no API required)."""

    supported_modalities: list[str] = ["text"]
    """This class only supports text input."""

    def __init__(self) -> None:
        """Initialize the mock embedding model."""
        super().__init__(model_name="mock-embedding-model", dimensions=1536)

    async def __call__(
        self,
        text: list[TextBlock | str],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Return fixed embeddings for testing."""
        import hashlib

        embeddings = []
        for t in text:
            # Extract text content
            if isinstance(t, dict):
                content = t.get("text", "")
            elif isinstance(t, TextBlock):
                content = t.text
            else:
                content = str(t)

            # Generate deterministic embedding based on text hash
            hash_val = int(hashlib.md5(content.encode()).hexdigest(), 16)
            # Create a 1536-dimensional embedding
            embedding = [(hash_val % 1000 + i) / 1000.0 for i in range(1536)]
            embeddings.append(embedding)

        return EmbeddingResponse(embeddings=embeddings)


class MockChatModel(ChatModelBase):
    """Mock chat model for testing (no API required)."""

    def __init__(self) -> None:
        """Initialize the mock chat model."""
        super().__init__(model_name="mock-chat-model", stream=False)

    async def __call__(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Return mock responses for entity/relationship extraction."""
        # Check if this is an entity extraction request
        last_message = messages[-1].get("content", "") if messages else ""

        # Mock entity extraction response
        if "entity" in last_message.lower() or "Entity Extraction" in last_message:
            mock_entities = {
                "entities": [
                    {
                        "name": "Alice",
                        "type": "PERSON",
                        "description": "A person",
                    },
                    {
                        "name": "OpenAI",
                        "type": "ORGANIZATION",
                        "description": "An AI company",
                    },
                    {
                        "name": "Bob",
                        "type": "PERSON",
                        "description": "A person",
                    },
                ],
            }
            return ChatResponse(
                content=[
                    TextBlock(type="text", text=json.dumps(mock_entities)),
                ],
            )

        # Mock relationship extraction response
        if "relationship" in last_message.lower():
            mock_relationships = {
                "relationships": [
                    {
                        "source": "Alice",
                        "target": "OpenAI",
                        "type": "WORKS_AT",
                        "description": "Alice works at OpenAI",
                    },
                    {
                        "source": "Alice",
                        "target": "Bob",
                        "type": "COLLABORATES_WITH",
                        "description": "Alice collaborates with Bob",
                    },
                ],
            }
            return ChatResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=json.dumps(mock_relationships),
                    ),
                ],
            )

        # Default response
        return ChatResponse(
            content=[TextBlock(type="text", text="Mock response")],
        )


@pytest.mark.skipif(
    not NEO4J_AVAILABLE,
    reason="Neo4j is not available. Set NEO4J_AVAILABLE=true to run these tests.",
)
class GraphKnowledgeTest(IsolatedAsyncioTestCase):
    """Test cases for GraphKnowledgeBase with real Neo4j."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures with real Neo4j."""
        # Use a unique collection name for each test run to avoid conflicts
        import time

        self.collection_name = f"test_{int(time.time() * 1000)}"

        # Initialize real Neo4j graph store
        self.graph_store = Neo4jGraphStore(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASSWORD,
            database=NEO4J_DATABASE,
            collection_name=self.collection_name,
            dimensions=1536,  # Mock embedding dimensions
        )

        # Use Mock models instead of real API calls
        self.embedding_model = MockTextEmbedding()
        self.llm_model = MockChatModel()

    async def asyncTearDown(self) -> None:
        """Clean up test data after each test."""
        try:
            # Clean up test collection from Neo4j
            driver = self.graph_store.get_client()
            async with driver.session(database=NEO4J_DATABASE) as session:
                # Delete all nodes with this collection name
                await session.run(
                    """
                    MATCH (n)
                    WHERE any(label IN labels(n) WHERE label ENDS WITH $suffix)
                    DETACH DELETE n
                    """,
                    {"suffix": f"_{self.collection_name}"},
                )
            await self.graph_store.close()
        except Exception as e:
            print(f"Warning: Failed to clean up test data: {e}")

    async def test_basic_vector_mode(self) -> None:
        """Test GraphKnowledgeBase in pure vector mode (no graph features)."""
        knowledge = GraphKnowledgeBase(
            graph_store=self.graph_store,
            embedding_model=self.embedding_model,
            llm_model=None,
            enable_entity_extraction=False,
            enable_relationship_extraction=False,
            enable_community_detection=False,
        )

        # Create test documents
        documents = [
            Document(
                id="doc1",
                metadata=DocMetadata(
                    content={
                        "type": "text",
                        "text": "Alice works at OpenAI as a researcher",
                    },
                    doc_id="doc1",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
            Document(
                id="doc2",
                metadata=DocMetadata(
                    content={
                        "type": "text",
                        "text": "Bob collaborates with Alice on AI research",
                    },
                    doc_id="doc2",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
        ]

        # Add documents
        await knowledge.add_documents(documents)

        # Test retrieval - with real Neo4j, we should get results
        results = await knowledge.retrieve(
            query="Who works at OpenAI?",
            limit=2,
            search_mode="vector",
        )

        # Verify results
        self.assertGreater(len(results), 0)
        self.assertIsNotNone(results[0].score)
        self.assertGreaterEqual(results[0].score, 0.0)
        self.assertLessEqual(results[0].score, 1.0)
        print(f"  ✓ Vector search returned {len(results)} results")

    async def test_with_entity_extraction(self) -> None:
        """Test GraphKnowledgeBase with entity extraction enabled."""
        knowledge = GraphKnowledgeBase(
            graph_store=self.graph_store,
            embedding_model=self.embedding_model,
            llm_model=self.llm_model,
            enable_entity_extraction=True,
            enable_relationship_extraction=False,
            enable_community_detection=False,
            entity_extraction_config={
                "max_entities_per_chunk": 10,
                "enable_gleanings": False,
            },
        )

        # Create test documents
        documents = [
            Document(
                id="doc1",
                metadata=DocMetadata(
                    content={
                        "type": "text",
                        "text": "Alice works at OpenAI as a research scientist specializing in large language models",
                    },
                    doc_id="doc1",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
        ]

        # Add documents (will extract entities using real LLM)
        print("  ⏳ Extracting entities using LLM...")
        await knowledge.add_documents(documents)
        print("  ✓ Entities extracted successfully")

    async def test_with_relationship_extraction(self) -> None:
        """Test GraphKnowledgeBase with entity and relationship extraction."""
        knowledge = GraphKnowledgeBase(
            graph_store=self.graph_store,
            embedding_model=self.embedding_model,
            llm_model=self.llm_model,
            enable_entity_extraction=True,
            enable_relationship_extraction=True,
            enable_community_detection=False,
            entity_extraction_config={
                "max_entities_per_chunk": 10,
                "enable_gleanings": False,
            },
        )

        # Create test documents
        documents = [
            Document(
                id="doc1",
                metadata=DocMetadata(
                    content={
                        "type": "text",
                        "text": "Alice works at OpenAI as a research scientist. She collaborates with Bob on transformer models.",
                    },
                    doc_id="doc1",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
        ]

        # Add documents (will extract entities and relationships using real LLM)
        print("  ⏳ Extracting entities and relationships using LLM...")
        await knowledge.add_documents(documents)
        print("  ✓ Entities and relationships extracted successfully")

    async def test_hybrid_search(self) -> None:
        """Test hybrid search mode (vector + graph)."""
        knowledge = GraphKnowledgeBase(
            graph_store=self.graph_store,
            embedding_model=self.embedding_model,
            llm_model=self.llm_model,
            enable_entity_extraction=True,
            enable_relationship_extraction=True,
            enable_community_detection=False,
            entity_extraction_config={
                "max_entities_per_chunk": 10,
                "enable_gleanings": False,
            },
        )

        # Add test documents
        documents = [
            Document(
                id="doc1",
                metadata=DocMetadata(
                    content={
                        "type": "text",
                        "text": "Alice works at OpenAI as a research scientist",
                    },
                    doc_id="doc1",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
            Document(
                id="doc2",
                metadata=DocMetadata(
                    content={
                        "type": "text",
                        "text": "Bob collaborates with Alice on AI research projects",
                    },
                    doc_id="doc2",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
        ]

        print("  ⏳ Adding documents with entity/relationship extraction...")
        await knowledge.add_documents(documents)

        # Test hybrid search
        print("  ⏳ Testing hybrid search...")
        results = await knowledge.retrieve(
            query="Who works at OpenAI?",
            limit=2,
            search_mode="hybrid",
            vector_weight=0.5,
            graph_weight=0.5,
        )

        self.assertGreater(len(results), 0)
        # Verify scores are present and reasonable
        for doc in results:
            self.assertIsNotNone(doc.score)
            self.assertGreaterEqual(doc.score, 0.0)
            self.assertLessEqual(doc.score, 1.0)
        print(f"  ✓ Hybrid search returned {len(results)} results")

    async def test_error_handling_no_llm(self) -> None:
        """Test that appropriate error is raised when LLM is required but not provided."""
        with self.assertRaises(ValueError) as context:
            GraphKnowledgeBase(
                graph_store=self.graph_store,
                embedding_model=self.embedding_model,
                llm_model=None,  # Missing LLM
                enable_entity_extraction=True,  # But entity extraction enabled
                enable_relationship_extraction=False,
                enable_community_detection=False,
            )
        print(f"  ✓ Correctly raised ValueError: {context.exception}")
