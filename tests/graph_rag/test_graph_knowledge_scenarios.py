# -*- coding: utf-8 -*-
"""Scenario-based tests for GraphKnowledgeBase.

This test file covers the main usage scenarios demonstrated in
examples/graph_rag/graph_knowledge_example.py:
1. Basic vector-only mode (no graph features)
2. Graph features mode (entity + relationship extraction)
"""
import asyncio

import pytest

from agentscope.rag import GraphKnowledgeBase


@pytest.mark.fast
@pytest.mark.asyncio
class TestVectorOnlyMode:
    """Scenario 1: Pure vector mode (example_basic_vector_only)."""

    async def test_add_and_retrieve_documents(
        self,
        vector_only_kb: GraphKnowledgeBase,
        simple_documents: list,
    ) -> None:
        """Test basic document addition and vector search."""
        # Add documents
        await vector_only_kb.add_documents(simple_documents)

        # Vector search
        results = await vector_only_kb.retrieve(
            query="Who works at OpenAI?",
            limit=2,
            search_mode="vector",
        )

        # Verify core behavior
        assert len(results) > 0, "Should return search results"
        assert results[0].score is not None, "Results should have scores"
        assert 0 <= results[0].score <= 1, "Scores should be normalized"

        # Verify results are sorted by score (descending)
        if len(results) > 1:
            assert (
                results[0].score is not None
            ), "First result should have score"
            assert (
                results[1].score is not None
            ), "Second result should have score"
            assert (
                results[0].score >= results[1].score
            ), "Results should be sorted by score"

    async def test_empty_documents_handling(
        self,
        vector_only_kb: GraphKnowledgeBase,
    ) -> None:
        """Test handling of empty document list."""
        # Should handle gracefully without error
        await vector_only_kb.add_documents([])

        # Verify no crash when retrieving from empty KB
        try:
            results = await vector_only_kb.retrieve(
                query="test",
                limit=5,
                search_mode="vector",
            )
            assert len(results) == 0, "Empty KB should return no results"
        except Exception as e:
            # Also acceptable: raise error when index doesn't exist
            assert "index" in str(e).lower() or "no such" in str(e).lower()

    async def test_multiple_queries(
        self,
        vector_only_kb: GraphKnowledgeBase,
        diverse_documents: list,
    ) -> None:
        """Test retrieval with different queries."""
        await vector_only_kb.add_documents(diverse_documents)

        # Test different queries
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
            assert (
                len(results) > 0
            ), f"Should return results for query: {query}"
            assert all(
                r.score is not None for r in results
            ), "All results should have scores"

    async def test_result_limit(
        self,
        vector_only_kb: GraphKnowledgeBase,
        diverse_documents: list,
    ) -> None:
        """Test that result limit is respected."""
        await vector_only_kb.add_documents(diverse_documents)

        for limit in [1, 2, 3]:
            results = await vector_only_kb.retrieve(
                query="technology",
                limit=limit,
                search_mode="vector",
            )
            assert (
                len(results) <= limit
            ), f"Should return at most {limit} results"


@pytest.mark.medium
@pytest.mark.asyncio
class TestGraphFeaturesMode:
    """Scenario 2: Graph features (example_with_graph_features)."""

    async def test_entity_and_relationship_extraction(
        self,
        full_graph_kb: GraphKnowledgeBase,
        entity_rich_documents: list,
    ) -> None:
        """Test entity and relationship extraction during addition."""
        # Add documents (automatically extracts entities and relationships)
        await full_graph_kb.add_documents(entity_rich_documents)

        # Wait for async processing
        await asyncio.sleep(2)

        # Test that documents are retrievable
        results = await full_graph_kb.retrieve(
            query="Alice Smith researcher",
            limit=3,
            search_mode="vector",
        )

        assert len(results) > 0, "Should retrieve documents after extraction"
        assert all(
            r.score is not None for r in results
        ), "All results should have scores"

    async def test_different_search_modes(
        self,
        full_graph_kb: GraphKnowledgeBase,
        entity_rich_documents: list,
    ) -> None:
        """Test vector, graph, and hybrid search modes."""
        await full_graph_kb.add_documents(entity_rich_documents)
        await asyncio.sleep(2)  # Wait for entity extraction

        query = "Tell me about Alice's work"

        # 1. Vector search (baseline)
        vector_results = await full_graph_kb.retrieve(
            query=query,
            limit=3,
            search_mode="vector",
        )
        assert len(vector_results) > 0, "Vector search should return results"

        # 2. Graph search (uses entity relationships)
        graph_results = await full_graph_kb.retrieve(
            query=query,
            limit=3,
            search_mode="graph",
            max_hops=2,
        )
        assert isinstance(
            graph_results,
            list,
        ), "Graph search should return a list"
        # Note: May return 0 results in mock environment

        # 3. Hybrid search (vector + graph)
        hybrid_results = await full_graph_kb.retrieve(
            query=query,
            limit=3,
            search_mode="hybrid",
            vector_weight=0.5,
            graph_weight=0.5,
        )
        assert len(hybrid_results) > 0, "Hybrid search should return results"
        assert all(
            r.score is not None and 0 <= r.score <= 1 for r in hybrid_results
        ), "Scores should be normalized"

    async def test_graph_search_with_different_hops(
        self,
        full_graph_kb: GraphKnowledgeBase,
        entity_rich_documents: list,
    ) -> None:
        """Test graph traversal with different max_hops settings."""
        await full_graph_kb.add_documents(entity_rich_documents)
        await asyncio.sleep(2)

        query = "collaborative AI research"

        # Test 1-hop and 2-hop graph search
        for max_hops in [1, 2]:
            results = await full_graph_kb.retrieve(
                query=query,
                limit=5,
                search_mode="graph",
                max_hops=max_hops,
            )
            assert isinstance(
                results,
                list,
            ), f"Should return list for {max_hops}-hop search"

    async def test_hybrid_search_weight_combinations(
        self,
        full_graph_kb: GraphKnowledgeBase,
        entity_rich_documents: list,
    ) -> None:
        """Test hybrid search with different weight combinations."""
        await full_graph_kb.add_documents(entity_rich_documents)
        await asyncio.sleep(2)

        query = "Alice research work"

        # Test different weight combinations
        weight_configs = [
            (0.9, 0.1),  # Vector-heavy
            (0.5, 0.5),  # Balanced
            (0.1, 0.9),  # Graph-heavy
        ]

        for vector_weight, graph_weight in weight_configs:
            results = await full_graph_kb.retrieve(
                query=query,
                limit=3,
                search_mode="hybrid",
                vector_weight=vector_weight,
                graph_weight=graph_weight,
            )
            # All weight combinations should return results
            assert len(results) > 0, (
                f"Should return results for "
                f"weights ({vector_weight}, {graph_weight})"
            )

    async def test_hybrid_search_score_correctness(
        self,
        full_graph_kb: GraphKnowledgeBase,
        entity_rich_documents: list,
    ) -> None:
        """Test that hybrid search correctly combines vector and graph scores.

        This test verifies that the bug where scores were assigned to
        doc.metadata.score instead of doc.score has been fixed.
        """
        await full_graph_kb.add_documents(entity_rich_documents)
        await asyncio.sleep(2)

        query = "Alice research work"

        # Execute hybrid search
        hybrid_results = await full_graph_kb.retrieve(
            query=query,
            limit=3,
            search_mode="hybrid",
            vector_weight=0.6,
            graph_weight=0.4,
        )

        # Verify results exist
        assert len(hybrid_results) > 0, "Hybrid search should return results"

        # Critical verification: score should be on Document, not metadata
        for result in hybrid_results:
            # Verify doc.score is set (this is where it should be)
            assert (
                result.score is not None
            ), "Document.score must be set by hybrid search"
            assert isinstance(
                result.score,
                float,
            ), "Document.score must be a float"
            assert (
                0 <= result.score <= 1
            ), "Document.score must be normalized [0,1]"

            # Verify metadata.score is NOT set (it shouldn't be after fix)
            # Use 'in' operator to check without triggering __getattr__
            try:
                # Attempt to access metadata.score
                metadata_score = result.metadata.get("score")
                if metadata_score is not None:
                    # If metadata.score was set (old bug behavior),
                    # it should not be used
                    assert False, (
                        f"Bug detected: metadata.score should not be set, "
                        f"but found value {metadata_score}. "
                        f"Score should only be in doc.score={result.score}"
                    )
            except (KeyError, AttributeError):
                # Good! metadata.score doesn't exist (expected after fix)
                pass


@pytest.mark.fast
class TestConfigurationOptions:
    """Test different configuration options for GraphKnowledgeBase."""

    def test_vector_only_configuration(
        self,
        vector_only_kb: GraphKnowledgeBase,
    ) -> None:
        """Verify vector-only mode configuration."""
        assert not vector_only_kb.enable_entity_extraction
        assert not vector_only_kb.enable_relationship_extraction
        assert not vector_only_kb.enable_community_detection

    def test_entity_extraction_configuration(
        self,
        entity_kb: GraphKnowledgeBase,
    ) -> None:
        """Verify entity extraction mode configuration."""
        assert entity_kb.enable_entity_extraction
        assert not entity_kb.enable_relationship_extraction
        assert entity_kb.llm_model is not None

        # Check entity extraction config
        config = entity_kb.entity_extraction_config
        assert "max_entities_per_chunk" in config
        assert config["max_entities_per_chunk"] == 10
        assert "enable_gleanings" in config
        assert not config["enable_gleanings"]

    def test_full_graph_configuration(
        self,
        full_graph_kb: GraphKnowledgeBase,
    ) -> None:
        """Verify full graph mode configuration."""
        assert full_graph_kb.enable_entity_extraction
        assert full_graph_kb.enable_relationship_extraction
        assert full_graph_kb.llm_model is not None
