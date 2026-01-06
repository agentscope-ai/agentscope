# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name
"""Pytest configuration and shared fixtures for GraphKnowledgeBase tests.

This module provides:
- Mock models (embedding and LLM) for testing without API calls
- Pytest fixtures for different KB configurations
- Sample document fixtures
- Helper functions for async operations

All tests use mock models by default to work in CI/CD environments
without Neo4j or external API access.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import pytest
import pytest_asyncio

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

# Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# Check if Neo4j is available (default: false for CI/CD)
NEO4J_AVAILABLE = os.getenv("NEO4J_AVAILABLE", "false").lower() == "true"


# Mock Models (no API required)
class MockTextEmbedding(EmbeddingModelBase):
    """Mock embedding model for testing (no API required)."""

    supported_modalities: list[str] = ["text"]

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
            # Note: TextBlock is a TypedDict, so we check dict structure
            if isinstance(t, dict):
                content = t.get("text", "")
            else:
                content = str(t)

            base_value = 0.5
            hash_val = int(hashlib.md5(content.encode()).hexdigest(), 16)

            embedding = []
            for i in range(1536):
                # Base value + small perturbation (0 to 0.1)
                perturbation = ((hash_val + i) % 100) / 1000.0
                embedding.append(base_value + perturbation)

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
        import re

        last_message = messages[-1].get("content", "") if messages else ""

        # Entity extraction: extract capitalized words from "Text: xxx"
        if "entity" in last_message.lower():
            match = re.search(
                r"Text:\s*(.+?)(?:\n\n|$)",
                last_message,
                re.DOTALL,
            )
            text = match.group(1) if match else ""

            # Extract capitalized words as entities
            names = list(set(re.findall(r"\b[A-Z][a-z]+", text)))[:5]
            entities = [
                {"name": name, "type": "CONCEPT", "description": name}
                for name in names
            ] or [
                {
                    "name": "Default",
                    "type": "CONCEPT",
                    "description": "Default",
                },
            ]

            return ChatResponse(
                content=[TextBlock(type="text", text=json.dumps(entities))],
            )

        # Relationship extraction: create chain relationships from entity list
        if "relationship" in last_message.lower():
            match = re.search(
                r"Known entities:\s*(.+?)(?:\n\n|$)",
                last_message,
            )
            names = (
                [n.strip() for n in match.group(1).split(",")[:4]]
                if match
                else []
            )

            relationships = [
                {
                    "source": names[i],
                    "target": names[i + 1],
                    "type": "RELATED",
                    "description": "",
                }
                for i in range(len(names) - 1)
            ]

            return ChatResponse(
                content=[
                    TextBlock(type="text", text=json.dumps(relationships)),
                ],
            )

        # Default response
        return ChatResponse(
            content=[TextBlock(type="text", text="Mock response")],
        )


# Pytest markers
def pytest_configure(config: Any) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "fast: Fast tests (<5s)")
    config.addinivalue_line("markers", "medium: Medium speed tests (5-15s)")
    config.addinivalue_line("markers", "slow: Slow tests (>15s)")
    config.addinivalue_line(
        "markers",
        "requires_gds: Requires Neo4j GDS plugin",
    )
    config.addinivalue_line(
        "markers",
        "requires_neo4j: Requires Neo4j database "
        "(skip if NEO4J_AVAILABLE is not true)",
    )


# Event loop fixture for async tests
@pytest.fixture(scope="session")
def event_loop() -> "Generator[asyncio.AbstractEventLoop, None, None]":
    """Create an event loop for the test session."""  # type: ignore
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# Collection name generator
@pytest.fixture
def collection_name() -> str:
    """Generate a unique collection name for each test."""
    return f"test_{int(time.time() * 1000)}"


# Graph store fixtures
@pytest_asyncio.fixture
async def graph_store(
    collection_name: str,
) -> AsyncGenerator[Neo4jGraphStore, None]:
    """Create a Neo4j graph store for testing."""
    if not NEO4J_AVAILABLE:
        pytest.skip(
            "Neo4j is not available. "
            "Set NEO4J_AVAILABLE=true to run these tests.",
        )

    store = Neo4jGraphStore(
        uri=NEO4J_URI,
        user=NEO4J_USER,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
        collection_name=collection_name,
        dimensions=1536,
    )

    yield store

    # Cleanup
    try:
        driver = store.get_client()
        async with driver.session(database=NEO4J_DATABASE) as session:
            await session.run(
                """
                MATCH (n)
                WHERE any(label IN labels(n) WHERE label ENDS WITH $suffix)
                DETACH DELETE n
                """,
                {"suffix": f"_{collection_name}"},
            )
        await store.close()
    except Exception as e:
        print(f"Warning: Failed to clean up test data: {e}")


# Embedding model fixture
@pytest.fixture
def embedding_model() -> MockTextEmbedding:
    """Create a mock embedding model for testing (no API required)."""
    return MockTextEmbedding()


# LLM model fixture
@pytest.fixture
def llm_model() -> MockChatModel:
    """Create a mock chat model for testing (no API required)."""
    return MockChatModel()


# Knowledge base fixtures
@pytest_asyncio.fixture
async def vector_only_kb(
    graph_store: Neo4jGraphStore,
    embedding_model: MockTextEmbedding,
) -> GraphKnowledgeBase:
    """Create a vector-only knowledge base (no graph features)."""
    return GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=None,
        enable_entity_extraction=False,
        enable_relationship_extraction=False,
        enable_community_detection=False,
    )


@pytest_asyncio.fixture
async def entity_kb(
    graph_store: Neo4jGraphStore,
    embedding_model: MockTextEmbedding,
    llm_model: MockChatModel,
) -> GraphKnowledgeBase:
    """Create a knowledge base with entity extraction."""
    return GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=llm_model,
        enable_entity_extraction=True,
        enable_relationship_extraction=False,
        enable_community_detection=False,
        entity_extraction_config={
            "max_entities_per_chunk": 10,
            "enable_gleanings": False,
        },
    )


@pytest_asyncio.fixture
async def full_graph_kb(
    graph_store: Neo4jGraphStore,
    embedding_model: MockTextEmbedding,
    llm_model: MockChatModel,
) -> GraphKnowledgeBase:
    """Create a knowledge base with entity and relationship extraction."""
    return GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=llm_model,
        enable_entity_extraction=True,
        enable_relationship_extraction=True,
        enable_community_detection=False,
        entity_extraction_config={
            "max_entities_per_chunk": 10,
            "enable_gleanings": False,
        },
    )


@pytest_asyncio.fixture
async def community_kb(
    graph_store: Neo4jGraphStore,
    embedding_model: MockTextEmbedding,
    llm_model: MockChatModel,
) -> GraphKnowledgeBase:
    """Create a KB with all features including community detection."""
    return GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=llm_model,
        enable_entity_extraction=True,
        enable_relationship_extraction=True,
        enable_community_detection=True,
        community_algorithm="leiden",
        entity_extraction_config={
            "max_entities_per_chunk": 15,
            "enable_gleanings": False,
        },
    )


# Sample documents fixtures
@pytest.fixture
def simple_documents() -> list[Document]:
    """Create simple test documents."""
    return [
        Document(
            id="simple_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "Alice works at OpenAI as a researcher.",
                },
                doc_id="simple_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="simple_2",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "Bob collaborates with Alice on AI research.",
                },
                doc_id="simple_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]


@pytest.fixture
def diverse_documents() -> list[Document]:
    """Create diverse documents with different relevance levels."""
    return [
        # High relevance - AI research
        Document(
            id="high_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "OpenAI conducts cutting-edge research in artificial "
                        "intelligence, focusing on large language models like "
                        "GPT-4."
                    ),
                },
                doc_id="high_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="high_2",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Google DeepMind in London pioneered breakthroughs in "
                        "deep reinforcement learning, including AlphaGo and "
                        "AlphaFold."
                    ),
                },
                doc_id="high_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        # Medium relevance
        Document(
            id="med_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Alice is a software engineer at a tech "
                        "startup in San Francisco."
                    ),
                },
                doc_id="med_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        # Low relevance
        Document(
            id="low_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Python is a popular programming language "
                        "used in web development."
                    ),
                },
                doc_id="low_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]


@pytest.fixture
def entity_rich_documents() -> list[Document]:
    """Create documents rich in entities and relationships."""
    return [
        Document(
            id="entity_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Alice Smith works at OpenAI in San Francisco as a "
                        "senior researcher specializing in transformer "
                        "architectures."
                    ),
                },
                doc_id="entity_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="entity_2",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Bob Johnson collaborates with Alice on the GPT-4 "
                        "project at OpenAI, focusing on model alignment and "
                        "safety."
                    ),
                },
                doc_id="entity_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="entity_3",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "OpenAI, headquartered in San Francisco, "
                        "partners with Microsoft to develop advanced "
                        "AI systems."
                    ),
                },
                doc_id="entity_3",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]


# Helper functions for async operations
async def wait_for_entities(
    graph_store: Any,  # pylint: disable=redefined-outer-name
    min_count: int = 1,
    timeout: int = 10,
) -> int:
    """Wait for entities to be created in Neo4j (used by some tests).

    Args:
        graph_store: Neo4j graph store instance
        min_count: Minimum number of entities expected
        timeout: Maximum wait time in seconds

    Returns:
        Number of entities found

    Raises:
        TimeoutError: If condition not met within timeout
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            driver = graph_store.get_client()
            async with driver.session(
                database=graph_store.database,
            ) as session:
                result = await session.run(
                    f"""
                    MATCH (e:Entity_{graph_store.collection_name})
                    RETURN count(e) as entity_count
                    """,
                )
                record = await result.single()
                count = record["entity_count"]
                if count >= min_count:
                    return count
        except Exception:
            pass

        await asyncio.sleep(0.5)

    raise TimeoutError(
        f"Expected at least {min_count} entities after {timeout}s",
    )
