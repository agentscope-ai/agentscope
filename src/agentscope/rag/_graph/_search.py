# -*- coding: utf-8 -*-
"""Search strategies for graph knowledge base."""

import asyncio
import math
from typing import Any

from ...exception import GraphQueryError
from ..._logging import logger
from .._document import DocMetadata
from .._reader import Document
from .._store import GraphStoreBase


class GraphSearch:
    """Graph knowledge base search strategies.

    This class provides multiple search modes:
    - Vector search: Pure similarity-based retrieval
    - Graph search: Relationship-aware traversal
    - Hybrid search: Combined vector + graph
    - Global search: Community-level understanding
    """

    # pylint: disable=too-few-public-methods

    graph_store: GraphStoreBase
    enable_community_detection: bool

    async def _vector_search(
        self,
        query_embedding: list[float],
        limit: int,
        score_threshold: float | None,
    ) -> list[Document]:
        """Pure vector similarity search.

        This is the fastest search mode and serves as the baseline.

        Args:
            query_embedding: Query embedding vector
            limit: Maximum number of documents
            score_threshold: Minimum similarity score

        Returns:
            List of relevant documents
        """
        logger.debug("Executing vector search")
        return await self.graph_store.search(
            query_embedding=query_embedding,
            limit=limit,
            score_threshold=score_threshold,
        )

    async def _graph_search(
        self,
        query_embedding: list[float],
        limit: int,
        score_threshold: float | None,  # pylint: disable=unused-argument
        **kwargs: Any,
    ) -> list[Document]:
        """Graph traversal-based search.

        This mode uses graph traversal to find documents that are
        structurally related to the query through entity relationships.

        Args:
            query_embedding: Query embedding vector
            limit: Maximum number of documents
            score_threshold: Minimum similarity score (unused here)
            **kwargs: Additional arguments:
                - max_hops: Maximum graph traversal hops (default: 2)

        Returns:
            List of relevant documents
        """
        logger.debug("Executing graph search")
        max_hops = kwargs.get("max_hops", 2)

        return await self.graph_store.search_with_graph(
            query_embedding=query_embedding,
            max_hops=max_hops,
            limit=limit,
        )

    async def _hybrid_search(
        self,
        query_embedding: list[float],
        limit: int,
        score_threshold: float | None,
        **kwargs: Any,
    ) -> list[Document]:
        """Hybrid search combining vector and graph methods.

        This mode executes vector and graph searches in parallel,
        then combines and re-ranks the results.

        Args:
            query_embedding: Query embedding vector
            limit: Maximum number of documents
            score_threshold: Minimum similarity score
            **kwargs: Additional arguments:
                - vector_weight: Weight for vector results (default: 0.5)
                - graph_weight: Weight for graph results (default: 0.5)
                - max_hops: Maximum graph traversal hops (default: 2)

        Returns:
            List of relevant documents (deduplicated and re-ranked)
        """
        logger.debug("Executing hybrid search")

        # Get weights
        vector_weight = kwargs.get("vector_weight", 0.5)
        graph_weight = kwargs.get("graph_weight", 0.5)

        # Execute both searches in parallel
        results_tuple = await asyncio.gather(
            self._vector_search(query_embedding, limit, score_threshold),
            self._graph_search(
                query_embedding,
                limit,
                score_threshold,
                **kwargs,
            ),
            return_exceptions=True,
        )

        # Handle exceptions and extract results
        vector_results_raw = results_tuple[0]
        graph_results_raw = results_tuple[1]

        vector_results: list[Document]
        graph_results: list[Document]

        if isinstance(vector_results_raw, Exception):
            logger.error("Vector search failed: %s", vector_results_raw)
            vector_results = []
        else:
            vector_results = vector_results_raw

        if isinstance(graph_results_raw, Exception):
            logger.error("Graph search failed: %s", graph_results_raw)
            graph_results = []
        else:
            graph_results = graph_results_raw

        # Combine and deduplicate results
        doc_scores: dict[str, float] = {}
        doc_map: dict[str, Document] = {}

        # Add vector results
        for doc in vector_results:
            doc_id = doc.id
            score = (doc.score or 0.0) * vector_weight
            doc_scores[doc_id] = doc_scores.get(doc_id, 0) + score
            doc_map[doc_id] = doc

        # Add graph results
        for doc in graph_results:
            doc_id = doc.id
            score = (doc.score or 0.0) * graph_weight
            doc_scores[doc_id] = doc_scores.get(doc_id, 0) + score
            doc_map[doc_id] = doc

        # Sort by combined score
        sorted_doc_ids = sorted(
            doc_scores.keys(),
            key=lambda x: doc_scores[x],
            reverse=True,
        )[:limit]

        # Build result list
        results = []
        for doc_id in sorted_doc_ids:
            doc = doc_map[doc_id]
            # Update with combined score
            doc.score = doc_scores[doc_id]
            results.append(doc)

        logger.debug(
            "Hybrid search: %s vector + %s graph = %s combined",
            len(vector_results),
            len(graph_results),
            len(results),
        )

        return results

    async def _global_search(
        self,
        query_embedding: list[float],
        limit: int,
        **kwargs: Any,
    ) -> list[Document]:
        """Global search using community summaries.

        This mode searches at the community level for global understanding
        and thematic queries. Requires community detection to be enabled.

        Process:
        1. Search for relevant communities using vector similarity
        2. Extract representative entities from top communities
        3. Find documents mentioning these entities
        4. Rank documents by community relevance and return top results

        Args:
            query_embedding: Query embedding vector
            limit: Maximum number of documents
            **kwargs: Additional arguments:
                - min_community_level: Minimum community level (default: 0)
                - max_entities_per_community: Max entities to extract
                    per community (default: 10)
                - community_limit: Max communities to consider (default: 5)

        Returns:
            List of relevant documents from top communities

        Raises:
            ValueError: If community detection is not enabled
            GraphQueryError: If retrieval fails
        """
        if not self.enable_community_detection:
            raise ValueError(
                "Global search requires community detection to be enabled. "
                "Set enable_community_detection=True when initializing.",
            )

        logger.debug("Executing global search")

        # Step 1: Search for relevant communities
        communities = await self._global_search_communities(
            query_embedding,
            **kwargs,
        )

        if not communities:
            logger.warning(
                "No communities found, falling back to vector search",
            )
            return await self._vector_search(query_embedding, limit, None)

        # Step 2: Extract entity weights from communities
        entity_weights = self._global_search_extract_entities(
            communities,
            **kwargs,
        )

        if not entity_weights:
            logger.warning("No entities found in communities")
            return await self._vector_search(query_embedding, limit, None)

        # Step 3: Find and rank documents
        return await self._global_search_find_documents(
            query_embedding,
            entity_weights,
            limit,
            len(communities),
        )

    async def _global_search_communities(
        self,
        query_embedding: list[float],
        **kwargs: Any,
    ) -> list[dict]:
        """Search for relevant communities (Step 1 of global search).

        Args:
            query_embedding: Query embedding vector
            **kwargs: Additional arguments

        Returns:
            List of relevant communities
        """
        min_level = kwargs.get("min_community_level", 0)
        community_limit = kwargs.get("community_limit", 5)

        communities = await self.graph_store.search_communities(
            query_embedding=query_embedding,
            min_level=min_level,
            limit=community_limit,
        )

        logger.debug("Found %s relevant communities", len(communities))
        return communities

    def _global_search_extract_entities(
        self,
        communities: list[dict],
        **kwargs: Any,
    ) -> dict[str, float]:
        """Extract entity weights from communities (Step 2 of global search).

        Args:
            communities: List of communities
            **kwargs: Additional arguments

        Returns:
            Dictionary mapping entity names to weights
        """
        max_entities_per_comm = kwargs.get("max_entities_per_community", 10)
        entity_weights: dict[str, float] = {}

        for comm in communities:
            comm_score = comm.get("score", 1.0)
            entity_ids = comm.get("entity_ids", [])[:max_entities_per_comm]

            for entity_name in entity_ids:
                # Higher score = higher weight
                if entity_name not in entity_weights:
                    entity_weights[entity_name] = comm_score
                else:
                    # If entity appears in multiple communities, use max score
                    entity_weights[entity_name] = max(
                        entity_weights[entity_name],
                        comm_score,
                    )

        logger.debug(
            "Extracted %s entities from communities",
            len(entity_weights),
        )
        return entity_weights

    async def _global_search_find_documents(
        self,
        query_embedding: list[float],
        entity_weights: dict[str, float],
        limit: int,
        community_count: int,
    ) -> list[Document]:
        """Find and rank documents (Step 3 of global search).

        Args:
            query_embedding: Query embedding vector
            entity_weights: Dictionary mapping entity names to weights
            limit: Maximum number of documents
            community_count: Number of communities searched

        Returns:
            List of ranked documents

        Raises:
            GraphQueryError: If query fails
        """
        entity_names = list(entity_weights.keys())

        try:
            driver = self.graph_store.get_client()
            async with driver.session(
                database=self.graph_store.database,
            ) as session:
                query = f"""
                MATCH (e:Entity_{self.graph_store.collection_name})
                WHERE e.name IN $entity_names
                MATCH (e)<-[m:MENTIONS]-(doc:Document_{
                    self.graph_store.collection_name
                })

                WITH doc,
                     count(DISTINCT e) AS entity_count,
                     sum(m.count) AS total_mentions,
                     collect(e.name) AS mentioned_entities,
                     gds.similarity.cosine(doc.embedding, \
$query_embedding) AS vector_similarity

                RETURN DISTINCT doc,
                       entity_count,
                       total_mentions,
                       mentioned_entities,
                       vector_similarity
                ORDER BY vector_similarity DESC, entity_count DESC, \
total_mentions DESC
                LIMIT $limit
                """

                result = await session.run(
                    query,
                    {
                        "entity_names": entity_names,
                        "query_embedding": query_embedding,
                        "limit": limit * 2,  # Get more to allow for scoring
                    },
                )

                # Calculate scores and convert to Document objects
                documents = []
                async for record in result:
                    node = record["doc"]
                    entity_count = record["entity_count"]
                    total_mentions = record["total_mentions"]
                    mentioned_entities = record["mentioned_entities"]
                    vector_similarity = record["vector_similarity"]

                    # Calculate entity weight contribution
                    entity_score_sum = sum(
                        entity_weights.get(name, 0.0)
                        for name in mentioned_entities
                    )

                    max_possible_score = len(entity_names) * max(
                        entity_weights.values(),
                    )
                    if max_possible_score > 0:
                        base_score = entity_score_sum / max_possible_score
                        entity_ratio = (
                            entity_count / len(entity_names)
                            if len(entity_names) > 0
                            else 0
                        )
                        mention_factor = math.log1p(
                            total_mentions,
                        ) / math.log1p(10)
                        mention_factor = min(mention_factor, 1.0)

                        # Weighted combination: vector similarity (60%) +
                        # community signals (40%)
                        doc_score = (
                            0.6 * vector_similarity
                            + 0.2 * base_score
                            + 0.1 * entity_ratio
                            + 0.1 * mention_factor
                        )
                    else:
                        doc_score = 0.0

                    doc = Document(
                        id=node["id"],
                        embedding=node["embedding"],
                        metadata=DocMetadata(
                            content={"type": "text", "text": node["content"]},
                            doc_id=node["doc_id"],
                            chunk_id=node["chunk_id"],
                            total_chunks=node["total_chunks"],
                        ),
                        score=doc_score,
                    )
                    documents.append(doc)

                # Sort by score and limit
                documents.sort(key=lambda d: d.score or 0.0, reverse=True)
                documents = documents[:limit]

                logger.debug(
                    "Global search found %s documents "
                    "from %s entities across %s communities",
                    len(documents),
                    len(entity_names),
                    community_count,
                )

                return documents

        except Exception as e:
            logger.error("Global search failed: %s", e)
            raise GraphQueryError(f"Global search failed: {e}") from e
