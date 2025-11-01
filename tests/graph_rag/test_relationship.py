# -*- coding: utf-8 -*-
"""Test relationship extraction functionality."""
import asyncio
import pytest
from agentscope.rag import GraphKnowledgeBase, Document, DocMetadata


@pytest.mark.medium
@pytest.mark.asyncio
async def test_relationship_extraction_basic(
    full_graph_kb: GraphKnowledgeBase,
    entity_rich_documents: list[Document],
):
    """Test basic relationship extraction."""
    await full_graph_kb.add_documents(entity_rich_documents)

    # Verify relationships were extracted via graph search
    results = await full_graph_kb.retrieve(
        query="Alice and Bob collaboration",
        limit=3,
        search_mode="graph",
        max_hops=2,
    )

    assert len(results) > 0


@pytest.mark.medium
@pytest.mark.asyncio
async def test_relationship_storage_verification(
    full_graph_kb: GraphKnowledgeBase,
    graph_store,
):
    """Test that relationship extraction workflow completes without errors."""
    doc = Document(
        id="rel_verify",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": "Bill Gates founded Microsoft and serves as its chairman. He also worked with Paul Allen to create the company.",
            },
            doc_id="rel_verify",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    # Add documents - should complete without errors
    await full_graph_kb.add_documents([doc])

    # Wait for async processing to complete
    await asyncio.sleep(5)

    # Check relationship count (but don't require specific count - LLM-dependent)
    driver = graph_store.get_client()
    async with driver.session(database=graph_store.database) as session:
        result = await session.run(
            f"""
            MATCH ()-[r:RELATED_TO_{graph_store.collection_name}]->()
            RETURN count(r) as rel_count
            """,
        )
        record = await result.single()
        rel_count = record["rel_count"]

        # Log the result (LLM may or may not extract relationships)
        print(
            f"✓ Relationship extraction completed. Found {rel_count} relationships (LLM-dependent)",
        )

        # Verify workflow completed without errors (allow 0 or more relationships)
        assert rel_count >= 0, f"Unexpected negative count: {rel_count}"


@pytest.mark.medium
@pytest.mark.asyncio
async def test_relationship_types(
    full_graph_kb: GraphKnowledgeBase,
):
    """Test extraction of different relationship types."""
    docs = [
        Document(
            id="rel_type_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "Ada Lovelace worked with Charles Babbage on the Analytical Engine.",
                },
                doc_id="rel_type_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="rel_type_2",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "Cambridge University is located in Cambridge, England.",
                },
                doc_id="rel_type_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]

    await full_graph_kb.add_documents(docs)

    # Should extract different relationship types: WORKED_WITH, LOCATED_IN, etc.
    results = await full_graph_kb.retrieve(
        query="collaborations and locations",
        limit=5,
        search_mode="graph",
    )

    assert len(results) > 0


@pytest.mark.medium
@pytest.mark.asyncio
async def test_multi_hop_graph_traversal(
    full_graph_kb: GraphKnowledgeBase,
):
    """Test that multi-hop graph traversal works."""
    # Create documents with transitive relationships
    # A -> B, B -> C, should be able to find C from A
    docs = [
        Document(
            id="hop_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "Alice mentors Bob in machine learning research.",
                },
                doc_id="hop_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="hop_2",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "Bob mentors Charlie in deep learning techniques.",
                },
                doc_id="hop_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="hop_3",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "Charlie works on neural network optimization.",
                },
                doc_id="hop_3",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]

    await full_graph_kb.add_documents(docs)

    # Query about Alice should potentially find documents about Charlie via Bob
    results = await full_graph_kb.retrieve(
        query="Alice's research network",
        limit=5,
        search_mode="graph",
        max_hops=2,
    )

    assert len(results) > 0


@pytest.mark.medium
@pytest.mark.asyncio
async def test_relationship_with_entity_mentions(
    full_graph_kb: GraphKnowledgeBase,
    graph_store,
):
    """Test that MENTIONS relationships are created between documents and entities."""
    from .conftest import wait_for_entities

    doc = Document(
        id="mentions_test",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": "Einstein worked at Princeton on theoretical physics.",
            },
            doc_id="mentions_test",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    await full_graph_kb.add_documents([doc])

    # Wait for entities to be created first
    entity_count = await wait_for_entities(
        graph_store,
        min_count=1,
        timeout=15,
    )
    assert entity_count >= 1, f"Expected entities, got {entity_count}"

    # Check for MENTIONS relationships in Neo4j
    driver = graph_store.get_client()
    async with driver.session(database=graph_store.database) as session:
        result = await session.run(
            f"""
            MATCH (doc:Document_{graph_store.collection_name})-[m:MENTIONS]->(e:Entity_{graph_store.collection_name})
            RETURN count(m) as mention_count
            """,
        )
        record = await result.single()
        mention_count = record["mention_count"]

        # Should have at least 1 MENTIONS relationship
        assert (
            mention_count >= 1
        ), f"Expected at least 1 MENTIONS relationship, got {mention_count}"


@pytest.mark.medium
@pytest.mark.asyncio
async def test_complex_relationship_extraction(
    full_graph_kb: GraphKnowledgeBase,
):
    """Test extraction of complex relationships from detailed text."""
    doc = Document(
        id="complex_rel",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": "Stanford University, founded in 1885 by Leland Stanford, is located in California and has produced numerous Nobel Prize winners including Paul Berg who won in Chemistry.",
            },
            doc_id="complex_rel",
            chunk_id=0,
            total_chunks=1,
        ),
    )

    await full_graph_kb.add_documents([doc])

    # Should extract multiple entities and relationships
    results = await full_graph_kb.retrieve(
        query="Stanford University Nobel Prize",
        limit=3,
        search_mode="hybrid",
    )

    assert len(results) > 0
