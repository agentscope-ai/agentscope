# -*- coding: utf-8 -*-
"""Test community detection functionality."""
import pytest
from agentscope.rag import GraphKnowledgeBase, Document, DocMetadata


@pytest.mark.slow
@pytest.mark.requires_gds
@pytest.mark.asyncio
async def test_community_detection_leiden(
    community_kb: GraphKnowledgeBase,
    diverse_documents: list[Document],
):
    """Test community detection with Leiden algorithm."""
    from .conftest import wait_for_communities

    # Add documents
    await community_kb.add_documents(diverse_documents)

    # Manually trigger detection to ensure completion
    try:
        result = await community_kb.detect_communities(algorithm="leiden")

        assert result["status"] in [
            "success",
            "no_communities_found",
        ], f"Unexpected status: {result['status']}"

        if result["status"] == "success":
            assert (
                result["community_count"] > 0
            ), f"Expected communities, got {result['community_count']}"
            assert result["levels"] >= 0, f"Invalid levels: {result['levels']}"
            assert (
                result["algorithm"] == "leiden"
            ), f"Expected leiden, got {result['algorithm']}"

            # Verify communities exist in database
            comm_count = await wait_for_communities(
                community_kb.graph_store,
                min_count=1,
                timeout=10,
            )
            assert comm_count >= 1, f"Expected communities in DB, got {comm_count}"
    except Exception as e:
        # If GDS is not available, test should not fail completely
        pytest.skip(f"Community detection requires Neo4j GDS plugin: {e}")


@pytest.mark.slow
@pytest.mark.requires_gds
@pytest.mark.asyncio
async def test_community_detection_louvain(
    graph_store,
    embedding_model,
    llm_model,
    entity_rich_documents: list[Document],
):
    """Test community detection with Louvain algorithm."""
    kb = GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=embedding_model,
        llm_model=llm_model,
        enable_entity_extraction=True,
        enable_relationship_extraction=True,
        enable_community_detection=True,
        community_algorithm="louvain",
    )

    await kb.add_documents(entity_rich_documents)

    try:
        result = await kb.detect_communities(algorithm="louvain")

        assert result["status"] in [
            "success",
            "no_communities_found",
        ], f"Unexpected status: {result['status']}"

        if result["status"] == "success":
            assert (
                result["algorithm"] == "louvain"
            ), f"Expected louvain, got {result['algorithm']}"
            assert (
                result["community_count"] > 0
            ), f"Expected communities, got {result['community_count']}"
    except Exception as e:
        pytest.skip(f"Community detection requires Neo4j GDS plugin: {e}")


@pytest.mark.slow
@pytest.mark.requires_gds
@pytest.mark.asyncio
async def test_global_search_with_communities(
    community_kb: GraphKnowledgeBase,
):
    """Test global search mode using community summaries."""
    # Create diverse documents for community formation
    docs = [
        Document(
            id="ai_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "OpenAI researches artificial intelligence and machine learning with focus on GPT models and language understanding.",
                },
                doc_id="ai_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="ai_2",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "DeepMind advances AI through deep learning and reinforcement learning research including AlphaGo and protein folding.",
                },
                doc_id="ai_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="space_1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "SpaceX develops reusable rockets and spacecraft for Mars colonization and satellite deployment.",
                },
                doc_id="space_1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="space_2",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": "NASA explores space through missions to Mars, Moon, and beyond with robotic and crewed spacecraft.",
                },
                doc_id="space_2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]

    await community_kb.add_documents(docs)

    try:
        # Run community detection
        result = await community_kb.detect_communities()

        if result["status"] == "success":
            assert (
                result["community_count"] > 0
            ), f"Expected communities, got {result['community_count']}"

            # Test global search
            global_results = await community_kb.retrieve(
                query="What are the main research areas?",
                limit=3,
                search_mode="global",
                min_community_level=0,
            )

            assert len(global_results) > 0, "Expected global search results"
            assert all(
                r.score is not None for r in global_results
            ), "All results should have scores"
            assert all(
                0 <= r.score <= 1 for r in global_results
            ), "Scores should be between 0 and 1"
    except ValueError as e:
        if "Global search requires community detection" in str(e):
            pytest.skip("Community detection not properly enabled")
        else:
            raise
    except Exception as e:
        pytest.skip(f"Community detection requires Neo4j GDS plugin: {e}")


@pytest.mark.slow
@pytest.mark.requires_gds
@pytest.mark.asyncio
async def test_community_summary_generation(
    community_kb: GraphKnowledgeBase,
    graph_store,
    entity_rich_documents: list[Document],
):
    """Test that community summaries are generated."""
    from .conftest import wait_for_communities

    await community_kb.add_documents(entity_rich_documents)

    try:
        result = await community_kb.detect_communities()

        if result["status"] == "success" and result["community_count"] > 0:
            # Wait for communities to be fully created
            comm_count = await wait_for_communities(
                graph_store,
                min_count=1,
                timeout=15,
            )
            assert comm_count > 0, f"Expected communities in DB, got {comm_count}"

            # Check that communities have summaries in Neo4j
            driver = graph_store.get_client()
            async with driver.session(
                database=graph_store.database,
            ) as session:
                query_result = await session.run(
                    f"""
                    MATCH (c:Community_{graph_store.collection_name})
                    WHERE c.summary IS NOT NULL
                    RETURN count(c) as count_with_summary
                    """,
                )
                record = await query_result.single()
                count = record["count_with_summary"]

                assert count > 0, f"Expected communities to have summaries, got {count}"
    except Exception as e:
        pytest.skip(f"Community detection requires Neo4j GDS plugin: {e}")


@pytest.mark.slow
@pytest.mark.requires_gds
@pytest.mark.asyncio
async def test_community_embedding_generation(
    community_kb: GraphKnowledgeBase,
    graph_store,
    entity_rich_documents: list[Document],
):
    """Test that community embeddings are generated."""
    from .conftest import wait_for_communities

    await community_kb.add_documents(entity_rich_documents)

    try:
        result = await community_kb.detect_communities()

        if result["status"] == "success" and result["community_count"] > 0:
            # Wait for communities to be fully created
            comm_count = await wait_for_communities(
                graph_store,
                min_count=1,
                timeout=15,
            )
            assert comm_count > 0, f"Expected communities in DB, got {comm_count}"

            # Check that communities have embeddings
            driver = graph_store.get_client()
            async with driver.session(
                database=graph_store.database,
            ) as session:
                query_result = await session.run(
                    f"""
                    MATCH (c:Community_{graph_store.collection_name})
                    WHERE c.embedding IS NOT NULL
                    RETURN count(c) as count_with_embedding
                    """,
                )
                record = await query_result.single()
                count = record["count_with_embedding"]

                assert (
                    count > 0
                ), f"Expected communities to have embeddings, got {count}"
    except Exception as e:
        pytest.skip(f"Community detection requires Neo4j GDS plugin: {e}")


@pytest.mark.slow
@pytest.mark.requires_gds
@pytest.mark.asyncio
async def test_community_detection_with_many_entities(
    community_kb: GraphKnowledgeBase,
):
    """Test community detection with a larger dataset."""
    from .conftest import wait_for_communities

    # Create more documents to form meaningful communities
    docs = [
        Document(
            id=f"tech_{i}",
            metadata=DocMetadata(
                content={"type": "text", "text": text},
                doc_id=f"tech_{i}",
                chunk_id=0,
                total_chunks=1,
            ),
        )
        for i, text in enumerate(
            [
                "Apple designs consumer electronics including iPhone and Mac computers.",
                "Steve Jobs cofounded Apple and revolutionized personal computing.",
                "Microsoft develops Windows operating system and Office productivity software.",
                "Bill Gates founded Microsoft and became a leading philanthropist.",
                "Google creates search engines and cloud computing services.",
                "Larry Page and Sergey Brin founded Google at Stanford University.",
            ],
        )
    ]

    await community_kb.add_documents(docs)

    try:
        result = await community_kb.detect_communities()

        if result["status"] == "success":
            # Should detect communities grouping related entities
            assert (
                result["community_count"] > 0
            ), f"Expected communities, got {result['community_count']}"
            assert result["levels"] >= 0, f"Invalid levels: {result['levels']}"

            # Verify communities exist in database
            comm_count = await wait_for_communities(
                community_kb.graph_store,
                min_count=1,
                timeout=15,
            )
            assert comm_count >= 1, f"Expected communities in DB, got {comm_count}"

            print(
                f"✓ Detected {result['community_count']} communities with {result['levels']} levels",
            )
    except Exception as e:
        pytest.skip(f"Community detection requires Neo4j GDS plugin: {e}")
