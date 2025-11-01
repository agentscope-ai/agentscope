# -*- coding: utf-8 -*-
"""Pytest configuration and shared fixtures for GraphKnowledgeBase tests."""
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

# Add src to path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

from agentscope.rag import (
    GraphKnowledgeBase,
    Neo4jGraphStore,
    Document,
    DocMetadata,
)
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.model import DashScopeChatModel

# Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "your api key")


# Pytest markers
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "fast: Fast tests (<5s)")
    config.addinivalue_line("markers", "medium: Medium speed tests (5-15s)")
    config.addinivalue_line("markers", "slow: Slow tests (>15s)")
    config.addinivalue_line("markers", "requires_gds: Requires Neo4j GDS plugin")


# Event loop fixture for async tests
@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
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
async def graph_store(collection_name: str) -> AsyncGenerator[Neo4jGraphStore, None]:
    """Create a Neo4j graph store for testing."""
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
                f"""
                MATCH (n)
                WHERE any(label IN labels(n) WHERE label ENDS WITH $suffix)
                DETACH DELETE n
                """,
                {"suffix": f"_{collection_name}"}
            )
        await store.close()
    except Exception as e:
        print(f"Warning: Failed to clean up test data: {e}")


# Embedding model fixture
@pytest.fixture
def embedding_model() -> DashScopeTextEmbedding:
    """Create an embedding model for testing."""
    return DashScopeTextEmbedding(
        model_name="text-embedding-v2",
        api_key=DASHSCOPE_API_KEY,
    )


# LLM model fixture
@pytest.fixture
def llm_model() -> DashScopeChatModel:
    """Create an LLM model for testing."""
    return DashScopeChatModel(
        model_name="qwen-max",
        api_key=DASHSCOPE_API_KEY,
        stream=False,
    )


# Knowledge base fixtures
@pytest_asyncio.fixture
async def vector_only_kb(
    graph_store: Neo4jGraphStore,
    embedding_model: DashScopeTextEmbedding,
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
    embedding_model: DashScopeTextEmbedding,
    llm_model: DashScopeChatModel,
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
    embedding_model: DashScopeTextEmbedding,
    llm_model: DashScopeChatModel,
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
    embedding_model: DashScopeTextEmbedding,
    llm_model: DashScopeChatModel,
) -> GraphKnowledgeBase:
    """Create a knowledge base with all features including community detection."""
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
                content={"type": "text", "text": "Alice works at OpenAI as a researcher."},
                doc_id="simple_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="simple_2",
            metadata=DocMetadata(
                content={"type": "text", "text": "Bob collaborates with Alice on AI research."},
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
                content={"type": "text", "text": "OpenAI conducts cutting-edge research in artificial intelligence, focusing on large language models like GPT-4."},
                doc_id="high_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="high_2",
            metadata=DocMetadata(
                content={"type": "text", "text": "Google DeepMind in London pioneered breakthroughs in deep reinforcement learning, including AlphaGo and AlphaFold."},
                doc_id="high_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        # Medium relevance
        Document(
            id="med_1",
            metadata=DocMetadata(
                content={"type": "text", "text": "Alice is a software engineer at a tech startup in San Francisco."},
                doc_id="med_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        # Low relevance
        Document(
            id="low_1",
            metadata=DocMetadata(
                content={"type": "text", "text": "Python is a popular programming language used in web development."},
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
                content={"type": "text", "text": "Alice Smith works at OpenAI in San Francisco as a senior researcher specializing in transformer architectures."},
                doc_id="entity_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="entity_2",
            metadata=DocMetadata(
                content={"type": "text", "text": "Bob Johnson collaborates with Alice on the GPT-4 project at OpenAI, focusing on model alignment and safety."},
                doc_id="entity_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="entity_3",
            metadata=DocMetadata(
                content={"type": "text", "text": "OpenAI, headquartered in San Francisco, partners with Microsoft to develop advanced AI systems."},
                doc_id="entity_3",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]


# Helper functions for polling async operations
async def wait_for_condition(
    check_func,
    condition,
    timeout: int = 10,
    interval: float = 0.5,
    error_msg: str = "Condition not met",
):
    """Poll until condition is met or timeout.
    
    Args:
        check_func: Async function to call repeatedly
        condition: Function to test the result
        timeout: Maximum wait time in seconds
        interval: Time between checks in seconds
        error_msg: Error message if timeout
        
    Returns:
        The result that satisfied the condition
        
    Raises:
        TimeoutError: If condition not met within timeout
    """
    import time
    start_time = time.time()
    last_result = None
    
    while time.time() - start_time < timeout:
        try:
            result = await check_func()
            last_result = result
            if condition(result):
                return result
        except Exception:
            # Log but continue polling
            pass
        
        await asyncio.sleep(interval)
    
    raise TimeoutError(
        f"{error_msg}. Timeout after {timeout}s. Last result: {last_result}"
    )


async def wait_for_entities(
    graph_store,
    min_count: int = 1,
    timeout: int = 10,
) -> int:
    """Wait for entities to be created in Neo4j."""
    async def check_entities():
        driver = graph_store.get_client()
        async with driver.session(database=graph_store.database) as session:
            result = await session.run(
                f"""
                MATCH (e:Entity_{graph_store.collection_name})
                RETURN count(e) as entity_count
                """
            )
            record = await result.single()
            return record["entity_count"]
    
    return await wait_for_condition(
        check_func=check_entities,
        condition=lambda count: count >= min_count,
        timeout=timeout,
        error_msg=f"Expected at least {min_count} entities",
    )


async def wait_for_relationships(
    graph_store,
    min_count: int = 1,
    timeout: int = 10,
) -> int:
    """Wait for relationships to be created in Neo4j."""
    async def check_relationships():
        driver = graph_store.get_client()
        async with driver.session(database=graph_store.database) as session:
            result = await session.run(
                f"""
                MATCH ()-[r:RELATED_TO_{graph_store.collection_name}]->()
                RETURN count(r) as rel_count
                """
            )
            record = await result.single()
            return record["rel_count"]
    
    return await wait_for_condition(
        check_func=check_relationships,
        condition=lambda count: count >= min_count,
        timeout=timeout,
        error_msg=f"Expected at least {min_count} relationships",
    )


async def wait_for_entity_embeddings(
    graph_store,
    min_count: int = 1,
    timeout: int = 10,
) -> int:
    """Wait for entity embeddings to be generated."""
    async def check_embeddings():
        driver = graph_store.get_client()
        async with driver.session(database=graph_store.database) as session:
            result = await session.run(
                f"""
                MATCH (e:Entity_{graph_store.collection_name})
                WHERE e.embedding IS NOT NULL
                RETURN count(e) as count_with_embedding
                """
            )
            record = await result.single()
            return record["count_with_embedding"]
    
    return await wait_for_condition(
        check_func=check_embeddings,
        condition=lambda count: count >= min_count,
        timeout=timeout,
        error_msg=f"Expected at least {min_count} entities with embeddings",
    )


async def wait_for_communities(
    graph_store,
    min_count: int = 1,
    timeout: int = 15,
) -> int:
    """Wait for communities to be created."""
    async def check_communities():
        driver = graph_store.get_client()
        async with driver.session(database=graph_store.database) as session:
            result = await session.run(
                f"""
                MATCH (c:Community_{graph_store.collection_name})
                RETURN count(c) as community_count
                """
            )
            record = await result.single()
            return record["community_count"]
    
    return await wait_for_condition(
        check_func=check_communities,
        condition=lambda count: count >= min_count,
        timeout=timeout,
        error_msg=f"Expected at least {min_count} communities",
    )

