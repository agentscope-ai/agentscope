# -*- coding: utf-8 -*-
"""Test error handling and edge cases for GraphKnowledgeBase.

This module tests critical error conditions and validation logic.
Focuses on configuration errors and invalid inputs that should
raise exceptions.
"""
import pytest

from agentscope.embedding import EmbeddingModelBase
from agentscope.rag import (
    DocMetadata,
    Document,
    GraphKnowledgeBase,
    Neo4jGraphStore,
)


@pytest.mark.fast
def test_missing_llm_error_entity_extraction(
    graph_store: Neo4jGraphStore,
    embedding_model: EmbeddingModelBase,
) -> None:
    """Test that ValueError is raised when LLM is required but not provided."""
    with pytest.raises(ValueError, match="llm_model is required"):
        GraphKnowledgeBase(
            graph_store=graph_store,
            embedding_model=embedding_model,
            llm_model=None,
            enable_entity_extraction=True,
            enable_relationship_extraction=False,
            enable_community_detection=False,
        )


@pytest.mark.fast
def test_missing_llm_error_relationship_extraction(
    graph_store: Neo4jGraphStore,
    embedding_model: EmbeddingModelBase,
) -> None:
    """Test ValueError for relationship extraction without LLM."""
    with pytest.raises(ValueError, match="llm_model is required"):
        GraphKnowledgeBase(
            graph_store=graph_store,
            embedding_model=embedding_model,
            llm_model=None,
            enable_entity_extraction=False,
            enable_relationship_extraction=True,
            enable_community_detection=False,
        )


@pytest.mark.medium
@pytest.mark.asyncio
async def test_invalid_search_mode(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
) -> None:
    """Test that invalid search mode raises ValueError."""
    await vector_only_kb.add_documents(simple_documents)

    with pytest.raises(ValueError, match="Invalid search_mode"):
        await vector_only_kb.retrieve(
            query="test",
            limit=5,
            search_mode="invalid_mode",
        )


@pytest.mark.medium
@pytest.mark.asyncio
async def test_global_search_without_community_detection(
    entity_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
) -> None:
    """Test global search without community detection raises error."""
    await entity_kb.add_documents(simple_documents)

    with pytest.raises(
        ValueError,
        match="Global search requires community detection",
    ):
        await entity_kb.retrieve(
            query="test",
            limit=5,
            search_mode="global",
        )


@pytest.mark.medium
@pytest.mark.asyncio
async def test_invalid_document_content_type(
    vector_only_kb: GraphKnowledgeBase,
) -> None:
    """Test handling of invalid document content type."""
    doc = Document(
        id="invalid_content",
        metadata=DocMetadata(
            content={
                "type": "image",
                "url": "http://example.com/image.jpg",
            },  # Not text
            doc_id="invalid_content",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    # Should raise ValueError for non-text content
    with pytest.raises(ValueError, match="does not contain text content"):
        await vector_only_kb.add_documents([doc])


@pytest.mark.fast
@pytest.mark.asyncio
async def test_retrieve_before_add(vector_only_kb: GraphKnowledgeBase) -> None:
    """Test retrieving from empty knowledge base."""
    from agentscope.exception import GraphQueryError

    try:
        results = await vector_only_kb.retrieve(
            query="anything",
            limit=5,
            search_mode="vector",
        )
        # If no exception, should return empty list
        assert len(results) == 0
    except GraphQueryError as e:
        # Also acceptable: raise error when vector index doesn't exist yet
        assert "index" in str(e).lower() or "no such" in str(e).lower()
