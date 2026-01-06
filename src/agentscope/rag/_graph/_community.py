# -*- coding: utf-8 -*-
"""Community detection for graph knowledge base."""

import asyncio
import json
from typing import Any

from ...embedding import EmbeddingModelBase
from ...exception import GraphQueryError
from ...model import ChatModelBase
from ..._logging import logger
from .._store import GraphStoreBase
from ._embedding import _clean_llm_json_response
from ._types import Community, CommunityAlgorithm


class GraphCommunity:
    """Community detection functionality.

    This class provides methods for:
    - Running community detection algorithms (Leiden, Louvain)
    - Generating community summaries using LLM
    - Creating community embeddings
    """

    # pylint: disable=too-few-public-methods

    graph_store: GraphStoreBase
    embedding_model: EmbeddingModelBase
    llm_model: ChatModelBase | None
    enable_community_detection: bool
    community_algorithm: CommunityAlgorithm

    async def detect_communities(
        self,
        algorithm: CommunityAlgorithm | None = None,
        **kwargs: Any,
    ) -> dict:
        """Manually trigger community detection.

        This method runs community detection on the graph using Neo4j GDS
        algorithms. It should be called:
        - After adding large batches of documents
        - Periodically to update communities
        - When the graph structure has changed significantly

        The method:
        1. Runs Neo4j GDS community detection (Leiden or Louvain)
        2. Generates community summaries using LLM
        3. Generates community embeddings
        4. Stores communities in graph database

        Args:
            algorithm: Override the default community detection algorithm
            **kwargs: Additional arguments (e.g., max_level)

        Returns:
            Dictionary with detection results:
            - community_count: Number of communities detected
            - levels: Number of hierarchical levels
            - algorithm: Algorithm used

        Raises:
            ValueError: If community detection is not enabled
        """
        if not self.enable_community_detection:
            raise ValueError(
                "Community detection is not enabled. "
                "Set enable_community_detection=True when initializing.",
            )

        algo = algorithm or self.community_algorithm
        logger.info("Starting community detection using %s algorithm", algo)

        try:
            # Step 1: Run Neo4j GDS community detection
            communities_raw = await self._run_gds_community_detection(
                algo,
                **kwargs,
            )

            if not communities_raw:
                logger.warning("No communities detected")
                return {
                    "community_count": 0,
                    "levels": 0,
                    "algorithm": algo,
                    "status": "no_communities_found",
                }

            logger.info("Detected %s raw communities", len(communities_raw))

            # Step 2: Generate community summaries (batch + concurrent)
            communities_with_summaries = await self._batch_generate_summaries(
                communities_raw,
            )

            # Step 3: Generate community embeddings (batch)
            communities_with_embeddings = await self._batch_embed_communities(
                communities_with_summaries,
            )

            # Step 4: Store communities in graph database
            communities_data = [
                {
                    "id": comm.id,
                    "level": comm.level,
                    "title": comm.title,
                    "summary": comm.summary,
                    "rating": comm.rating,
                    "entity_count": comm.entity_count,
                    "entity_ids": comm.entity_ids,
                    "embedding": comm.embedding,
                }
                for comm in communities_with_embeddings
            ]

            await self.graph_store.add_communities(communities_data)

            # Calculate levels
            levels = (
                max(comm.level for comm in communities_with_embeddings) + 1
            )

            logger.info(
                "Community detection completed: %s communities across "
                "%s levels",
                len(communities_with_embeddings),
                levels,
            )

            return {
                "community_count": len(communities_with_embeddings),
                "levels": levels,
                "algorithm": algo,
                "status": "success",
            }

        except Exception as e:
            logger.error("Community detection failed: %s", e)
            raise GraphQueryError(f"Community detection failed: {e}") from e

    async def _run_gds_community_detection(
        self,
        algorithm: CommunityAlgorithm,
        **kwargs: Any,
    ) -> list[Community]:
        """Run Neo4j GDS community detection algorithm.

        This method:
        1. Creates a graph projection
        2. Runs the selected algorithm (Leiden or Louvain)
        3. Writes results back to nodes
        4. Retrieves community information
        5. Cleans up the projection

        Args:
            algorithm: Community detection algorithm
            **kwargs: Additional algorithm parameters (reserved for future use)

        Returns:
            List of raw communities (without summaries or embeddings)
        """
        # Reserved for future extension
        _ = kwargs

        driver = self.graph_store.get_client()

        try:
            async with driver.session(
                database=self.graph_store.database,
            ) as session:
                # Step 1: Create graph projection
                projection_name = (
                    f"entity_graph_{self.graph_store.collection_name}"
                )

                # Drop existing projection if any
                try:
                    await session.run(
                        f"CALL gds.graph.drop('{projection_name}', false)",
                    )
                except Exception as e:
                    # Projection doesn't exist, which is expected on first run
                    logger.debug(
                        "Graph projection '%s' does not exist (expected): %s",
                        projection_name,
                        e,
                    )

                # Create new projection
                project_query = f"""
                CALL gds.graph.project(
                    '{projection_name}',
                    'Entity_{self.graph_store.collection_name}',
                    {{
                        RELATED_TO: {{
                            orientation: 'UNDIRECTED',
                            properties: ['strength']
                        }}
                    }}
                )
                """
                await session.run(project_query)

                # Step 2: Run community detection algorithm
                if algorithm == "leiden":
                    algo_query = f"""
                    CALL gds.leiden.write(
                        '{projection_name}',
                        {{
                            writeProperty: 'community_id',
                            includeIntermediateCommunities: true
                        }}
                    )
                    YIELD communityCount
                    """
                else:  # louvain
                    algo_query = f"""
                    CALL gds.louvain.write(
                        '{projection_name}',
                        {{
                            writeProperty: 'community_id',
                            includeIntermediateCommunities: true
                        }}
                    )
                    YIELD communityCount
                    """

                result = await session.run(algo_query)
                stats = await result.single()

                logger.info(
                    "Algorithm stats: %s communities detected",
                    stats["communityCount"],
                )

                # Step 3: Retrieve community information
                retrieve_query = f"""
                MATCH (e:Entity_{self.graph_store.collection_name})
                WHERE e.community_id IS NOT NULL
                RETURN e.community_id AS community_id,
                       e.name AS entity_name,
                       e.description AS entity_description,
                       0 AS level
                ORDER BY e.community_id
                """

                result = await session.run(retrieve_query)

                # Group entities by community
                community_map: dict[int, dict] = {}
                async for record in result:
                    comm_id = record["community_id"]
                    # Handle both single value and list
                    # (from includeIntermediateCommunities)
                    if isinstance(comm_id, list):
                        # Use the highest level community
                        comm_id = comm_id[-1]
                    if comm_id not in community_map:
                        community_map[comm_id] = {
                            "id": f"comm_{comm_id}",
                            "level": record["level"],
                            "entity_names": [],
                            "entity_descriptions": [],
                        }
                    community_map[comm_id]["entity_names"].append(
                        record["entity_name"],
                    )
                    community_map[comm_id]["entity_descriptions"].append(
                        record["entity_description"],
                    )

                # Step 4: Clean up graph projection
                await session.run(f"CALL gds.graph.drop('{projection_name}')")

                # Create Community objects (without summaries/embeddings)
                communities = []
                for comm_data in community_map.values():
                    # Generate a simple placeholder summary to satisfy
                    # Pydantic validation
                    entity_preview = ", ".join(comm_data["entity_names"][:3])
                    placeholder_summary = (
                        f"Community of {len(comm_data['entity_names'])} "
                        f"entities: {entity_preview}"
                    )
                    if len(comm_data["entity_names"]) > 3:
                        placeholder_summary += ", ..."

                    community = Community(
                        id=comm_data["id"],
                        level=comm_data["level"],
                        title=f"Community {comm_data['id']}",  # Placeholder
                        summary=placeholder_summary,  # Temporary, will be
                        # replaced by LLM
                        rating=0.0,
                        entity_count=len(comm_data["entity_names"]),
                        entity_ids=comm_data["entity_names"],
                    )
                    # Store descriptions for summary generation
                    community.__dict__["_entity_descriptions"] = comm_data[
                        "entity_descriptions"
                    ]
                    communities.append(community)

                return communities

        except Exception as e:
            logger.error("GDS community detection failed: %s", e)
            # Try to clean up projection on error
            try:
                await session.run(
                    f"CALL gds.graph.drop('{projection_name}', false)",
                )
            except Exception as cleanup_error:
                logger.debug(
                    "Failed to clean up graph projection '%s' after error: %s",
                    projection_name,
                    cleanup_error,
                )
            raise

    async def _batch_generate_summaries(
        self,
        communities: list[Community],
    ) -> list[Community]:
        """Batch generate community summaries using LLM.

        This method uses asyncio.gather for concurrent LLM calls,
        with a semaphore to limit concurrency and avoid rate limits.

        Args:
            communities: List of communities without summaries

        Returns:
            List of communities with summaries
        """
        if not self.llm_model:
            logger.warning("No LLM model, using simple summaries")
            # Inline simple summary generation
            for comm in communities:
                comm.title = f"Group of {comm.entity_count} entities"
                entity_list = ", ".join(comm.entity_ids[:5])
                comm.summary = f"A community containing: {entity_list}"
                comm.rating = 0.5
            return communities

        logger.info(
            "Generating summaries for %s communities",
            len(communities),
        )

        # Limit concurrent LLM calls
        max_concurrent = 5
        semaphore = asyncio.Semaphore(max_concurrent)

        async def generate_summary(comm: Community) -> Community:
            async with semaphore:
                return await self._generate_community_summary(comm)

        tasks = [generate_summary(comm) for comm in communities]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle results and exceptions
        communities_with_summaries: list[Community] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Summary generation failed for %s: %s",
                    communities[i].id,
                    result,
                )
                # Use simple summary as fallback
                comm = communities[i]
                comm.title = f"Group of {comm.entity_count} entities"
                comm.summary = (
                    f"A community containing: {', '.join(comm.entity_ids[:5])}"
                )
                communities_with_summaries.append(comm)
            elif isinstance(result, Community):
                communities_with_summaries.append(result)

        return communities_with_summaries

    async def _generate_community_summary(
        self,
        community: Community,
    ) -> Community:
        """Generate summary for a single community using LLM.

        Args:
            community: Community without summary

        Returns:
            Community with generated summary and title
        """
        entity_names_str = ", ".join(community.entity_ids[:20])
        entity_descs = community.__dict__.get("_entity_descriptions", [])
        entity_descs_str = "; ".join(entity_descs[:10])

        prompt = f"""Summarize the following group of entities into a \
cohesive theme. You must return ONLY a valid JSON object with no additional \
text.

Entities: {entity_names_str}

Entity descriptions: {entity_descs_str}

Requirements:
1. Provide a brief summary (2-3 sentences) describing what these \
entities have in common
2. Provide a short title (3-5 words) for this community
3. The JSON must have exactly these fields: "title", "summary"
4. Return ONLY the JSON object, no markdown, no code blocks, no \
explanations

Expected format:
{{"title": "short title", "summary": "2-3 sentence summary describing \
the common theme"}}

JSON object:"""

        try:
            response_text = await self._call_llm(prompt)
            if not response_text:
                response_text = "{}"

            # Clean and parse JSON
            json_text = _clean_llm_json_response(response_text)
            data = json.loads(json_text)

            community.title = data.get("title", f"Community {community.id}")
            summary = data.get("summary", "").strip()

            # Ensure summary is not empty (Pydantic validation
            # requirement)
            if not summary:
                entity_list = ", ".join(community.entity_ids[:5])
                summary = f"A community containing: {entity_list}"

            community.summary = summary

            # Calculate rating based on entity count (simple
            # heuristic)
            community.rating = min(1.0, community.entity_count / 10.0)

            return community

        except Exception as e:
            logger.error("Failed to generate summary: %s", e)
            # Fallback to simple summary
            community.title = f"Group of {community.entity_count} entities"
            entity_list = ", ".join(community.entity_ids[:5])
            community.summary = f"A community containing: {entity_list}"
            community.rating = 0.5
            return community

    async def _batch_embed_communities(
        self,
        communities: list[Community],
    ) -> list[Community]:
        """Batch generate embeddings for community summaries.

        Following the "content embedding" principle, community embeddings
        contain ONLY the community summary (generated by LLM), NOT the
        entity list or relationships.

        Args:
            communities: List of communities with summaries

        Returns:
            List of communities with embeddings
        """
        logger.info(
            "Generating embeddings for %s communities",
            len(communities),
        )

        # Extract summaries
        summaries = [comm.summary for comm in communities]

        # Generate embeddings (batch processing)
        response = await self.embedding_model(summaries)

        # Attach embeddings and validate
        embeddings_ok = 0
        for comm, embedding in zip(communities, response.embeddings):
            comm.embedding = embedding
            if embedding and len(embedding) > 0:
                embeddings_ok += 1

        logger.info(
            "Successfully generated embeddings for %s/%s communities",
            embeddings_ok,
            len(communities),
        )

        return communities

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM - placeholder for type checker."""
        raise NotImplementedError("Must be provided by GraphEmbedding")
