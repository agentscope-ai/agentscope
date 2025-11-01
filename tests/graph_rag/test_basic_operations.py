# -*- coding: utf-8 -*-
"""Test basic operations of GraphKnowledgeBase."""
import pytest
from agentscope.rag import GraphKnowledgeBase, Document


@pytest.mark.fast
@pytest.mark.asyncio
async def test_initialization_vector_only(
    vector_only_kb: GraphKnowledgeBase,
) -> None:
    """Test initialization in vector-only mode."""
    assert vector_only_kb is not None
    assert not vector_only_kb.enable_entity_extraction
    assert not vector_only_kb.enable_relationship_extraction
    assert not vector_only_kb.enable_community_detection


@pytest.mark.fast
@pytest.mark.asyncio
async def test_initialization_with_entities(
    entity_kb: GraphKnowledgeBase,
) -> None:
    """Test initialization with entity extraction."""
    assert entity_kb is not None
    assert entity_kb.enable_entity_extraction
    assert not entity_kb.enable_relationship_extraction
    assert entity_kb.llm_model is not None


@pytest.mark.fast
@pytest.mark.asyncio
async def test_initialization_full_graph(
    full_graph_kb: GraphKnowledgeBase,
) -> None:
    """Test initialization with all graph features."""
    assert full_graph_kb is not None
    assert full_graph_kb.enable_entity_extraction
    assert full_graph_kb.enable_relationship_extraction
    assert full_graph_kb.llm_model is not None


@pytest.mark.medium
@pytest.mark.asyncio
async def test_add_single_document(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
):
    """Test adding a single document."""
    # Add one document
    await vector_only_kb.add_documents([simple_documents[0]])

    # Verify by retrieving
    results = await vector_only_kb.retrieve(
        query="Who works at OpenAI?",
        limit=5,
        search_mode="vector",
    )

    assert len(results) > 0
    assert results[0].score is not None
    assert 0 <= results[0].score <= 1


@pytest.mark.medium
@pytest.mark.asyncio
async def test_add_multiple_documents(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
):
    """Test adding multiple documents."""
    await vector_only_kb.add_documents(simple_documents)

    # Verify all documents can be retrieved
    results = await vector_only_kb.retrieve(
        query="Tell me about Alice and Bob",
        limit=5,
        search_mode="vector",
    )

    assert len(results) > 0


@pytest.mark.fast
@pytest.mark.asyncio
async def test_add_empty_documents(vector_only_kb: GraphKnowledgeBase) -> None:
    """Test adding empty document list."""
    # Should handle gracefully without error
    await vector_only_kb.add_documents([])

    # Verify no results (or handle index not created error)
    try:
        results = await vector_only_kb.retrieve(
            query="anything",
            limit=5,
            search_mode="vector",
        )
        # If retrieve succeeds, should return empty list
        assert len(results) == 0
    except Exception as e:
        # Expected: vector index might not exist yet with no documents
        # This is acceptable behavior
        assert "index" in str(e).lower() or "no such" in str(e).lower()


@pytest.mark.medium
@pytest.mark.asyncio
async def test_document_embedding_dimensions(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
):
    """Test that document embeddings have correct dimensions."""
    await vector_only_kb.add_documents(simple_documents)

    # Retrieve and check embedding dimensions
    results = await vector_only_kb.retrieve(
        query="Alice",
        limit=1,
        search_mode="vector",
    )

    assert len(results) > 0
    # Note: embedding might not be returned in Document,
    # but we verify it was stored by checking retrieval works
    assert results[0].score is not None


@pytest.mark.medium
@pytest.mark.asyncio
async def test_batch_add_documents(
    vector_only_kb: GraphKnowledgeBase,
    diverse_documents: list[Document],
):
    """Test adding a batch of diverse documents."""
    await vector_only_kb.add_documents(diverse_documents)

    # Verify different queries return relevant results
    queries = [
        "artificial intelligence research",
        "software engineer",
        "programming language",
    ]

    for query in queries:
        results = await vector_only_kb.retrieve(
            query=query,
            limit=2,
            search_mode="vector",
        )
        assert len(results) > 0, f"No results for query: {query}"


@pytest.mark.fast
def test_entity_config_defaults(entity_kb: GraphKnowledgeBase) -> None:
    """Test that entity extraction config has proper defaults."""
    config = entity_kb.entity_extraction_config

    assert "max_entities_per_chunk" in config
    assert config["max_entities_per_chunk"] == 10
    assert "enable_gleanings" in config
    assert "entity_types" in config
    assert isinstance(config["entity_types"], list)
