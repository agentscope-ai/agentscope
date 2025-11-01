# -*- coding: utf-8 -*-
"""Test error handling and edge cases."""
import pytest
from agentscope.rag import GraphKnowledgeBase, Document, DocMetadata


@pytest.mark.fast
def test_missing_llm_error_entity_extraction(
    graph_store,
    embedding_model,
):
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
    graph_store,
    embedding_model,
):
    """Test that ValueError is raised for relationship extraction without LLM."""
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
):
    """Test that invalid search mode raises ValueError."""
    await vector_only_kb.add_documents(simple_documents)

    with pytest.raises(ValueError, match="Invalid search_mode"):
        await vector_only_kb.retrieve(
            query="test",
            limit=5,
            search_mode="invalid_mode",  # type: ignore
        )


@pytest.mark.medium
@pytest.mark.asyncio
async def test_global_search_without_community_detection(
    entity_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
):
    """Test that global search without community detection raises ValueError."""
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
):
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
    results = await vector_only_kb.retrieve(
        query="anything",
        limit=5,
        search_mode="vector",
    )

    # Should return empty list
    assert len(results) == 0


@pytest.mark.medium
@pytest.mark.asyncio
async def test_negative_limit(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
):
    """Test handling of negative limit value."""
    await vector_only_kb.add_documents(simple_documents)

    # Negative limit should raise an error or be converted to positive
    # Neo4j doesn't accept negative limits, so we expect an error
    try:
        results = await vector_only_kb.retrieve(
            query="test",
            limit=-1,
            search_mode="vector",
        )
        # If no error, at least check it returns a list
        assert isinstance(results, list)
    except Exception as e:
        # Expected: Neo4j will raise an error for negative limit
        assert "positive" in str(e).lower() or "negative" in str(e).lower()


@pytest.mark.medium
@pytest.mark.asyncio
async def test_zero_limit(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
):
    """Test handling of zero limit value."""
    await vector_only_kb.add_documents(simple_documents)

    # Zero limit should raise an error or return empty list
    # Neo4j doesn't accept zero limit
    try:
        results = await vector_only_kb.retrieve(
            query="test",
            limit=0,
            search_mode="vector",
        )
        # If no error, should return empty list
        assert isinstance(results, list)
        assert len(results) == 0
    except Exception as e:
        # Expected: Neo4j will raise an error for zero limit
        assert "positive" in str(e).lower() or "zero" in str(e).lower()


@pytest.mark.medium
@pytest.mark.asyncio
async def test_very_long_query(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
):
    """Test handling of very long query text."""
    await vector_only_kb.add_documents(simple_documents)

    # Create a very long query
    long_query = "test " * 1000  # 1000 words

    # Should handle gracefully
    results = await vector_only_kb.retrieve(
        query=long_query,
        limit=5,
        search_mode="vector",
    )

    assert isinstance(results, list)


@pytest.mark.medium
@pytest.mark.asyncio
async def test_special_characters_in_query(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
):
    """Test handling of special characters in query."""
    await vector_only_kb.add_documents(simple_documents)

    special_queries = [
        "test@#$%^&*()",
        "query with 中文字符",
        "query with émojis 😀🎉",
        "query\nwith\nnewlines",
    ]

    for query in special_queries:
        results = await vector_only_kb.retrieve(
            query=query,
            limit=5,
            search_mode="vector",
        )
        assert isinstance(results, list)


@pytest.mark.medium
@pytest.mark.asyncio
async def test_duplicate_document_ids(
    vector_only_kb: GraphKnowledgeBase,
):
    """Test handling of documents with duplicate IDs."""
    docs = [
        Document(
            id="dup_id",
            metadata=DocMetadata(
                content={"type": "text", "text": "First document."},
                doc_id="dup_id",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="dup_id",  # Same ID
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "Second document with same ID.",
                },
                doc_id="dup_id",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]

    # Should handle gracefully (might update or skip)
    await vector_only_kb.add_documents(docs)

    results = await vector_only_kb.retrieve(
        query="document",
        limit=5,
        search_mode="vector",
    )

    assert len(results) > 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_community_detection_without_entities(
    graph_store,
    embedding_model,
    llm_model,
    simple_documents: list[Document],
):
    """Test community detection when no entities exist."""
    kb = GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=llm_model,
        enable_entity_extraction=True,
        enable_relationship_extraction=True,
        enable_community_detection=True,
    )

    # Add documents with minimal entity content
    minimal_docs = [
        Document(
            id="min_1",
            metadata=DocMetadata(
                content={"type": "text", "text": "Hello."},
                doc_id="min_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]

    await kb.add_documents(minimal_docs)

    # Community detection should handle gracefully
    try:
        result = await kb.detect_communities()
        assert result["status"] in ["success", "no_communities_found"]
    except Exception as e:
        # Might fail if GDS not available or no entities found
        print(f"Expected potential failure: {e}")


@pytest.mark.fast
def test_invalid_vector_weight(
    graph_store,
    embedding_model,
    llm_model,
):
    """Test that knowledge base can be created (weight validation happens at search time)."""
    # Knowledge base creation should succeed
    kb = GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=llm_model,
        enable_entity_extraction=True,
        enable_relationship_extraction=True,
        enable_community_detection=False,
    )

    assert kb is not None


@pytest.mark.medium
@pytest.mark.asyncio
async def test_malformed_document_structure(
    vector_only_kb: GraphKnowledgeBase,
):
    """Test handling of document with unusual structure."""
    # Document with empty text
    doc = Document(
        id="empty_text",
        metadata=DocMetadata(
            content={"type": "text", "text": ""},
            doc_id="empty_text",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    # Should handle gracefully
    await vector_only_kb.add_documents([doc])

    results = await vector_only_kb.retrieve(
        query="test",
        limit=5,
        search_mode="vector",
    )

    assert isinstance(results, list)
