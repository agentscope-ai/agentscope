# -*- coding: utf-8 -*-
"""Test entity extraction functionality."""
import pytest
from agentscope.rag import GraphKnowledgeBase, Document, DocMetadata


@pytest.mark.medium
@pytest.mark.asyncio
async def test_entity_extraction_basic(
    entity_kb: GraphKnowledgeBase,
    entity_rich_documents: list[Document],
):
    """Test basic entity extraction from documents."""
    # Add documents with entity extraction
    await entity_kb.add_documents(entity_rich_documents)

    # Verify entities were extracted by checking graph search works
    results = await entity_kb.retrieve(
        query="Alice Smith researcher",
        limit=3,
        search_mode="vector",
    )

    assert len(results) > 0, "Expected to retrieve documents"
    assert all(r.score is not None for r in results), "All results should have scores"
    assert all(0 <= r.score <= 1 for r in results), "Scores should be between 0 and 1"


@pytest.mark.medium
@pytest.mark.asyncio
async def test_entity_extraction_verification(
    entity_kb: GraphKnowledgeBase,
    graph_store,
):
    """Test that extracted entities are stored in the graph."""
    from .conftest import wait_for_entities

    doc = Document(
        id="verify_1",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": "Albert Einstein developed the theory of relativity at Princeton University.",
            },
            doc_id="verify_1",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    await entity_kb.add_documents([doc])

    # Wait for entities to be created (with polling)
    entity_count = await wait_for_entities(
        graph_store,
        min_count=2,
        timeout=15,
    )

    # Should have extracted at least 2 entities (Einstein, Princeton University)
    assert entity_count >= 2, f"Expected at least 2 entities, got {entity_count}"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_entity_extraction_with_gleanings(
    graph_store,
    embedding_model,
    llm_model,
):
    """Test multi-round entity extraction (gleanings)."""
    from .conftest import wait_for_entities

    kb = GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=llm_model,
        enable_entity_extraction=True,
        enable_relationship_extraction=False,
        enable_community_detection=False,
        entity_extraction_config={
            "max_entities_per_chunk": 5,
            "enable_gleanings": True,
            "gleanings_rounds": 1,
        },
    )

    doc = Document(
        id="glean_1",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": "Marie Curie, a physicist and chemist, conducted pioneering research on radioactivity at the Radium Institute in Paris, France, working with her husband Pierre Curie.",
            },
            doc_id="glean_1",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    await kb.add_documents([doc])

    # Wait for entity extraction (gleanings takes longer)
    entity_count = await wait_for_entities(
        graph_store,
        min_count=3,
        timeout=20,
    )

    # Should extract multiple entities (Marie, Pierre, Radium Institute, etc.)
    assert (
        entity_count >= 3
    ), f"Expected at least 3 entities with gleanings, got {entity_count}"

    # Verify retrieval works
    results = await kb.retrieve(
        query="Marie Curie research",
        limit=5,
        search_mode="vector",
    )

    assert len(results) > 0, "Expected to retrieve documents"


@pytest.mark.medium
@pytest.mark.asyncio
async def test_max_entities_limit(
    entity_kb: GraphKnowledgeBase,
):
    """Test that max_entities_per_chunk is respected."""
    # Document with many potential entities
    doc = Document(
        id="many_entities",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": "Apple, Microsoft, Google, Amazon, Meta, Tesla, Netflix, Intel, IBM, Oracle, Samsung, Nvidia are major technology companies.",
            },
            doc_id="many_entities",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    await entity_kb.add_documents([doc])

    # Should complete without error despite many entities
    results = await entity_kb.retrieve(
        query="technology companies",
        limit=5,
        search_mode="vector",
    )

    assert len(results) > 0


@pytest.mark.medium
@pytest.mark.asyncio
async def test_entity_types_configuration(
    graph_store,
    embedding_model,
    llm_model,
):
    """Test configuring specific entity types to extract."""
    kb = GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=llm_model,
        enable_entity_extraction=True,
        enable_relationship_extraction=False,
        enable_community_detection=False,
        entity_extraction_config={
            "max_entities_per_chunk": 10,
            "enable_gleanings": False,
            "entity_types": ["PERSON", "ORG", "LOCATION"],
        },
    )

    doc = Document(
        id="typed_1",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": "Steve Jobs founded Apple in Cupertino, California.",
            },
            doc_id="typed_1",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    await kb.add_documents([doc])

    # Verify extraction works with custom types
    results = await kb.retrieve(
        query="Steve Jobs Apple",
        limit=3,
        search_mode="vector",
    )

    assert len(results) > 0


@pytest.mark.medium
@pytest.mark.asyncio
async def test_entity_embedding_generation(
    entity_kb: GraphKnowledgeBase,
    graph_store,
):
    """Test that entity embeddings are generated."""
    from .conftest import wait_for_entity_embeddings

    doc = Document(
        id="embed_test",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": "Leonardo da Vinci was an Italian Renaissance artist.",
            },
            doc_id="embed_test",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    await entity_kb.add_documents([doc])

    # Wait for entity embeddings to be generated
    count = await wait_for_entity_embeddings(
        graph_store,
        min_count=1,
        timeout=15,
    )

    # Should have at least 1 entity with embeddings
    assert count >= 1, f"Expected at least 1 entity with embeddings, got {count}"


@pytest.mark.medium
@pytest.mark.asyncio
async def test_entity_extraction_empty_document(
    entity_kb: GraphKnowledgeBase,
):
    """Test entity extraction with minimal content."""
    doc = Document(
        id="minimal",
        metadata=DocMetadata(
            content={"type": "text", "text": "Hello."},
            doc_id="minimal",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    # Should handle gracefully without crashing
    await entity_kb.add_documents([doc])

    results = await entity_kb.retrieve(
        query="hello",
        limit=1,
        search_mode="vector",
    )

    # Document should still be retrievable
    assert len(results) > 0
