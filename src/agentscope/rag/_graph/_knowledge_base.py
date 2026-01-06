# -*- coding: utf-8 -*-
"""Graph-based knowledge base implementation using graph databases.

This module provides a knowledge base implementation that leverages
graph databases (e.g., Neo4j) to represent and retrieve knowledge
with entity and relationship awareness.
"""

import asyncio
from typing import Any

from ..._logging import logger
from ._types import CommunityAlgorithm, SearchMode
from .._knowledge_base import KnowledgeBase
from .._reader import Document
from .._store import GraphStoreBase
from ...embedding import EmbeddingModelBase
from ...model import ChatModelBase
from ._community import GraphCommunity
from ._embedding import GraphEmbedding
from ._entity import GraphEntity
from ._relationship import GraphRelationship
from ._search import GraphSearch


class GraphKnowledgeBase(
    GraphEmbedding,
    GraphSearch,
    GraphEntity,
    GraphRelationship,
    GraphCommunity,
    KnowledgeBase,
):
    """Graph-based knowledge base implementation.

    This knowledge base uses a graph database (GraphStoreBase) as the
    storage backend and provides advanced features including:

    - Entity extraction from documents
    - Relationship extraction between entities
    - Graph traversal-based retrieval
    - Community detection for global understanding
    - Multiple search modes (vector, graph, hybrid, global)

    The class follows the design principle of "content embedding + graph
    traversal":
    - Embeddings contain only content (documents, entities, communities)
    - Relationships are stored in graph structure
    - Graph traversal is used during retrieval to leverage relationships

    Attributes:
        graph_store: Graph database store (GraphStoreBase)
        embedding_model: Embedding model for generating vectors
        llm_model: Language model for entity/relationship extraction
        enable_entity_extraction: Whether to extract entities
        enable_relationship_extraction: Whether to extract relationships
        enable_community_detection: Whether to enable community detection

    Example:
        >>> # Basic usage (pure vector mode)
        >>> knowledge = GraphKnowledgeBase(
        ...     graph_store=Neo4jGraphStore(...),
        ...     embedding_model=DashScopeTextEmbedding(...),
        ...     llm_model=None,  # Disable graph features
        ...     enable_entity_extraction=False,
        ...     enable_relationship_extraction=False,
        ... )
        >>> await knowledge.add_documents(documents)
        >>> results = await knowledge.retrieve(query, search_mode="vector")

        >>> # Advanced usage (with graph features)
        >>> knowledge = GraphKnowledgeBase(
        ...     graph_store=Neo4jGraphStore(...),
        ...     embedding_model=DashScopeTextEmbedding(...),
        ...     llm_model=DashScopeChat(...),
        ...     enable_entity_extraction=True,
        ...     enable_relationship_extraction=True,
        ...     enable_community_detection=True,
        ... )
        >>> await knowledge.add_documents(documents)
        >>> results = await knowledge.retrieve(query, search_mode="hybrid")
    """

    def __init__(
        self,
        graph_store: GraphStoreBase,
        embedding_model: EmbeddingModelBase,
        llm_model: ChatModelBase | None = None,
        # Entity extraction config
        enable_entity_extraction: bool = True,
        entity_extraction_config: dict | None = None,
        # Relationship extraction config
        enable_relationship_extraction: bool = True,
        # Community detection config
        enable_community_detection: bool = False,
        community_algorithm: CommunityAlgorithm = "leiden",
    ) -> None:
        """Initialize graph knowledge base.

        Args:
            graph_store: Graph database store backend
            embedding_model: Embedding model for vector generation
            llm_model: Language model for entity/relationship extraction
                (required if entity_extraction or relationship_extraction
                is enabled)
            enable_entity_extraction: Whether to extract entities from
                documents
            entity_extraction_config: Entity extraction configuration dict:
                - max_entities_per_chunk: Max entities per document
                    (default: 10)
                - enable_gleanings: Enable multi-round extraction
                    (default: False)
                - gleanings_rounds: Number of gleaning rounds (default: 2)
                - entity_types: List of entity types (default: standard
                    types)
                - generate_entity_embeddings: Generate entity embeddings
                    (default: True)
            enable_relationship_extraction: Whether to extract relationships
            enable_community_detection: Whether to enable community
                detection (if True, first add_documents call triggers
                detection)
            community_algorithm: Community detection algorithm (leiden
                or louvain)

        Raises:
            ValueError: If entity/relationship extraction is enabled but
                llm_model is None
        """
        # Initialize parent class
        super().__init__(
            embedding_store=graph_store,
            embedding_model=embedding_model,
        )

        # Store references
        self.graph_store = graph_store
        self.llm_model = llm_model

        # Validate configuration
        if (
            enable_entity_extraction or enable_relationship_extraction
        ) and llm_model is None:
            raise ValueError(
                "llm_model is required when entity_extraction or "
                "relationship_extraction is enabled",
            )

        # Entity extraction config
        self.enable_entity_extraction = enable_entity_extraction
        self.entity_extraction_config = entity_extraction_config or {}
        self._entity_config_defaults()

        # Relationship extraction config
        self.enable_relationship_extraction = enable_relationship_extraction

        # Community detection config
        self.enable_community_detection = enable_community_detection
        self.community_algorithm = community_algorithm
        # Track first call for auto-detection
        self._first_add_documents_called = False

        logger.info(
            "Initialized GraphKnowledgeBase: "
            "entity_extraction=%s, "
            "relationship_extraction=%s, "
            "community_detection=%s",
            enable_entity_extraction,
            enable_relationship_extraction,
            enable_community_detection,
        )

    def _entity_config_defaults(self) -> None:
        """Set default values for entity extraction config."""
        self.entity_extraction_config.setdefault("max_entities_per_chunk", 10)
        self.entity_extraction_config.setdefault("enable_gleanings", False)
        self.entity_extraction_config.setdefault("gleanings_rounds", 2)
        self.entity_extraction_config.setdefault(
            "entity_types",
            ["PERSON", "ORG", "LOCATION", "PRODUCT", "EVENT", "CONCEPT"],
        )
        self.entity_extraction_config.setdefault(
            "generate_entity_embeddings",
            True,
        )

    # === Core KnowledgeBase methods ===

    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to the graph knowledge base.

        Process:
        1. Generate document embeddings
        2. Store documents in graph database
        3. [Optional] Extract entities from documents
        4. [Optional] Extract relationships between entities
        5. [Optional] Trigger community detection (first call only if enabled)

        Args:
            documents: List of documents to add
            **kwargs: Additional arguments (unused)

        Raises:
            GraphQueryError: If document storage fails
            EntityExtractionError: If entity extraction fails (when
                configured to raise)
        """
        if not documents:
            logger.warning("No documents to add")
            return

        try:
            # Step 1: Generate document embeddings
            logger.info(
                "Generating embeddings for %s documents",
                len(documents),
            )
            documents_with_embeddings = await self._embed_documents(documents)

            # Step 2: Store documents in graph database
            logger.info(
                "Adding %s documents to graph store",
                len(documents_with_embeddings),
            )
            await self.graph_store.add(documents_with_embeddings)

            # Step 3 & 4: Extract entities and relationships if enabled
            if self.enable_entity_extraction:
                await self._process_entities_and_relationships(
                    documents_with_embeddings,
                )

            # Step 5: Trigger community detection (first call only)
            if (
                self.enable_community_detection
                and not self._first_add_documents_called
            ):
                self._first_add_documents_called = True
                logger.info(
                    "First add_documents call - triggering community "
                    "detection in background",
                )
                asyncio.create_task(self.detect_communities())

            logger.info(
                "Successfully added %s documents to knowledge base",
                len(documents),
            )

        except Exception as e:
            logger.error("Failed to add documents: %s", e)
            raise

    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float | None = None,
        search_mode: SearchMode = "hybrid",
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant documents from the knowledge base.

        Supports multiple search modes:
        - vector: Pure vector similarity search (fastest)
        - graph: Graph traversal-based search (relationship-aware)
        - hybrid: Combined vector + graph search (recommended)
        - global: Community-level search (requires community detection)

        Args:
            query: Query string
            limit: Maximum number of documents to return
            score_threshold: Minimum similarity score threshold
            search_mode: Search mode to use
            **kwargs: Additional search arguments:
                - max_hops: Max graph traversal hops (for graph/hybrid modes)
                - vector_weight: Weight for vector results (for hybrid mode)
                - graph_weight: Weight for graph results (for hybrid mode)
                - min_community_level: Min community level (for global mode)

        Returns:
            List of relevant documents sorted by relevance

        Raises:
            ValueError: If invalid search_mode is provided
            GraphQueryError: If retrieval fails
        """
        try:
            # Generate query embedding
            query_embedding = await self._embed_query(query)

            # Dispatch to appropriate search method
            if search_mode == "vector":
                return await self._vector_search(
                    query_embedding,
                    limit,
                    score_threshold,
                )
            elif search_mode == "graph":
                return await self._graph_search(
                    query_embedding,
                    limit,
                    score_threshold,
                    **kwargs,
                )
            elif search_mode == "hybrid":
                return await self._hybrid_search(
                    query_embedding,
                    limit,
                    score_threshold,
                    **kwargs,
                )
            elif search_mode == "global":
                return await self._global_search(
                    query_embedding,
                    limit,
                    **kwargs,
                )
            else:
                raise ValueError(f"Invalid search_mode: {search_mode}")

        except Exception as e:
            logger.error("Retrieval failed: %s", e)
            raise
