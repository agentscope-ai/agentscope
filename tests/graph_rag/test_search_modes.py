# -*- coding: utf-8 -*-
"""Test different search modes of GraphKnowledgeBase."""
import pytest
from agentscope.rag import GraphKnowledgeBase, Document


@pytest.mark.medium
@pytest.mark.asyncio
async def test_vector_search(
    vector_only_kb: GraphKnowledgeBase,
    diverse_documents: list[Document],
) -> None:
    """Test pure vector search mode."""
    await vector_only_kb.add_documents(diverse_documents)

    results = await vector_only_kb.retrieve(
        query="artificial intelligence research organizations",
        limit=3,
        search_mode="vector",
    )

    assert len(results) > 0
    assert all(r.score is not None for r in results)
    assert all(0 <= r.score <= 1 for r in results)
    # Results should be sorted by score
    if len(results) > 1:
        assert results[0].score >= results[1].score


@pytest.mark.medium
@pytest.mark.asyncio
async def test_graph_search(
    full_graph_kb: GraphKnowledgeBase,
    entity_rich_documents: list[Document],
) -> None:
    """Test graph traversal search mode."""
    await full_graph_kb.add_documents(entity_rich_documents)

    results = await full_graph_kb.retrieve(
        query="Who works on AI research?",
        limit=3,
        search_mode="graph",
        max_hops=2,
    )

    assert len(results) > 0
    assert all(r.score is not None for r in results)


@pytest.mark.medium
@pytest.mark.asyncio
async def test_hybrid_search(
    full_graph_kb: GraphKnowledgeBase,
    entity_rich_documents: list[Document],
) -> None:
    """Test hybrid search mode (vector + graph)."""
    await full_graph_kb.add_documents(entity_rich_documents)

    results = await full_graph_kb.retrieve(
        query="OpenAI research projects",
        limit=3,
        search_mode="hybrid",
        vector_weight=0.5,
        graph_weight=0.5,
    )

    assert len(results) > 0
    assert all(r.score is not None for r in results)
    # Scores should be normalized
    assert all(0 <= r.score <= 1 for r in results)


@pytest.mark.medium
@pytest.mark.asyncio
async def test_search_with_score_threshold(
    vector_only_kb: GraphKnowledgeBase,
    diverse_documents: list[Document],
) -> None:
    """Test filtering results by score threshold."""
    await vector_only_kb.add_documents(diverse_documents)

    # High threshold should return fewer results
    high_threshold_results = await vector_only_kb.retrieve(
        query="artificial intelligence",
        limit=10,
        search_mode="vector",
        score_threshold=0.8,
    )

    # Low threshold should return more results
    low_threshold_results = await vector_only_kb.retrieve(
        query="artificial intelligence",
        limit=10,
        search_mode="vector",
        score_threshold=0.3,
    )

    # All results should meet threshold
    assert all(r.score >= 0.8 for r in high_threshold_results)
    assert all(r.score >= 0.3 for r in low_threshold_results)
    assert len(low_threshold_results) >= len(high_threshold_results)


@pytest.mark.medium
@pytest.mark.asyncio
async def test_search_with_limit(
    vector_only_kb: GraphKnowledgeBase,
    diverse_documents: list[Document],
) -> None:
    """Test limiting number of results."""
    await vector_only_kb.add_documents(diverse_documents)

    for limit in [1, 2, 3]:
        results = await vector_only_kb.retrieve(
            query="technology",
            limit=limit,
            search_mode="vector",
        )

        assert len(results) <= limit


@pytest.mark.medium
@pytest.mark.asyncio
async def test_hybrid_search_different_weights(
    full_graph_kb: GraphKnowledgeBase,
    entity_rich_documents: list[Document],
) -> None:
    """Test hybrid search with different weight combinations."""
    await full_graph_kb.add_documents(entity_rich_documents)

    query = "Alice research work"

    # Vector-heavy
    vector_heavy = await full_graph_kb.retrieve(
        query=query,
        limit=3,
        search_mode="hybrid",
        vector_weight=0.9,
        graph_weight=0.1,
    )

    # Graph-heavy
    graph_heavy = await full_graph_kb.retrieve(
        query=query,
        limit=3,
        search_mode="hybrid",
        vector_weight=0.1,
        graph_weight=0.9,
    )

    # Balanced
    balanced = await full_graph_kb.retrieve(
        query=query,
        limit=3,
        search_mode="hybrid",
        vector_weight=0.5,
        graph_weight=0.5,
    )

    # All should return results
    assert len(vector_heavy) > 0
    assert len(graph_heavy) > 0
    assert len(balanced) > 0


@pytest.mark.medium
@pytest.mark.asyncio
async def test_graph_search_different_max_hops(
    full_graph_kb: GraphKnowledgeBase,
    entity_rich_documents: list[Document],
) -> None:
    """Test graph search with different max_hops settings."""
    await full_graph_kb.add_documents(entity_rich_documents)

    query = "collaborative AI research"

    # 1 hop
    results_1hop = await full_graph_kb.retrieve(
        query=query,
        limit=5,
        search_mode="graph",
        max_hops=1,
    )

    # 2 hops (should potentially find more connections)
    results_2hop = await full_graph_kb.retrieve(
        query=query,
        limit=5,
        search_mode="graph",
        max_hops=2,
    )

    assert len(results_1hop) > 0
    assert len(results_2hop) > 0


@pytest.mark.medium
@pytest.mark.asyncio
async def test_empty_query_handling(
    vector_only_kb: GraphKnowledgeBase,
    simple_documents: list[Document],
) -> None:
    """Test handling of empty or very short queries."""
    await vector_only_kb.add_documents(simple_documents)

    # Empty query might return results with low scores
    results = await vector_only_kb.retrieve(
        query="",
        limit=2,
        search_mode="vector",
    )

    # Should handle gracefully (might return 0 or low-score results)
    assert isinstance(results, list)


@pytest.mark.medium
@pytest.mark.asyncio
async def test_relevance_ranking(
    vector_only_kb: GraphKnowledgeBase,
    diverse_documents: list[Document],
) -> None:
    """Test that results are properly ranked by relevance."""
    await vector_only_kb.add_documents(diverse_documents)

    # Query highly specific to one document
    results = await vector_only_kb.retrieve(
        query="DeepMind AlphaGo reinforcement learning London",
        limit=5,
        search_mode="vector",
    )

    assert len(results) > 0

    # The most relevant document should be ranked first
    # (document about DeepMind)
    top_result = results[0]
    assert (
        "DeepMind" in top_result.metadata.content["text"]
        or "AlphaGo" in top_result.metadata.content["text"]
    )
