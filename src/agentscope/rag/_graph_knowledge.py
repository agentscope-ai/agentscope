# -*- coding: utf-8 -*-
"""Graph-based knowledge base implementation using graph databases.

This module provides a knowledge base implementation that leverages
graph databases (e.g., Neo4j) to represent and retrieve knowledge
with entity and relationship awareness.
"""

import asyncio
import json
import logging
import math
from typing import Any

from agentscope.exception import GraphQueryError

from ._document import DocMetadata
from ._graph_types import (
    Community,
    CommunityAlgorithm,
    Entity,
    Relationship,
    SearchMode,
)
from ._knowledge_base import KnowledgeBase
from ._reader import Document
from ._store import GraphStoreBase
from ..embedding import EmbeddingModelBase
from ..model import ChatModelBase

logger = logging.getLogger(__name__)


def _extract_text_content(document: Document) -> str:
    """Extract text content from a Document.

    Args:
        document: Document object

    Returns:
        Text content as string

    Raises:
        ValueError: If content is not a TextBlock or doesn't contain text
    """
    content = document.metadata.content
    if isinstance(content, dict) and content.get("type") == "text":
        text = content.get("text", "")
        return str(text) if text is not None else ""
    else:
        raise ValueError(
            f"Document {document.id} does not contain text content. "
            f"Only TextBlock is supported for graph knowledge base.",
        )


class GraphKnowledgeBase(KnowledgeBase):
    """Graph-based knowledge base implementation.

    This knowledge base uses a graph database (GraphStoreBase) as the
    storage backend and provides advanced features including:

    - Entity extraction from documents
    - Relationship extraction between entities
    - Graph traversal-based retrieval
    - Community detection for global understanding
    - Multiple search modes (vector, graph, hybrid, global)

    The class follows the design principle of "content embedding + graph traversal":
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
                      (required if entity_extraction or relationship_extraction is enabled)
            enable_entity_extraction: Whether to extract entities from documents
            entity_extraction_config: Entity extraction configuration dict:
                - max_entities_per_chunk: Max entities per document (default: 10)
                - enable_gleanings: Enable multi-round extraction (default: False)
                - gleanings_rounds: Number of gleaning rounds (default: 2)
                - entity_types: List of entity types (default: standard types)
                - generate_entity_embeddings: Generate entity embeddings (default: True)
            enable_relationship_extraction: Whether to extract relationships
            enable_community_detection: Whether to enable community detection
                                       (if True, first add_documents call triggers detection)
            community_algorithm: Community detection algorithm (leiden or louvain)

        Raises:
            ValueError: If entity/relationship extraction is enabled but llm_model is None
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
        self._first_add_documents_called = False  # Track first call for auto-detection

        logger.info(
            f"Initialized GraphKnowledgeBase: "
            f"entity_extraction={enable_entity_extraction}, "
            f"relationship_extraction={enable_relationship_extraction}, "
            f"community_detection={enable_community_detection}",
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
            EntityExtractionError: If entity extraction fails (when configured to raise)
        """
        if not documents:
            logger.warning("No documents to add")
            return

        try:
            # Step 1: Generate document embeddings
            logger.info(
                f"Generating embeddings for {len(documents)} documents",
            )
            documents_with_embeddings = await self._embed_documents(documents)

            # Step 2: Store documents in graph database
            logger.info(
                f"Adding {len(documents_with_embeddings)} documents to graph store",
            )
            await self.graph_store.add(documents_with_embeddings)

            # Step 3 & 4: Extract entities and relationships if enabled
            if self.enable_entity_extraction:
                await self._process_entities_and_relationships(
                    documents_with_embeddings,
                )

            # Step 5: Trigger community detection (first call only)
            if self.enable_community_detection and not self._first_add_documents_called:
                self._first_add_documents_called = True
                logger.info(
                    "First add_documents call - triggering community detection in background",
                )
                asyncio.create_task(self.detect_communities())

            logger.info(
                f"Successfully added {len(documents)} documents to knowledge base",
            )

        except Exception as e:
            logger.error(f"Failed to add documents: {e}")
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
            logger.error(f"Retrieval failed: {e}")
            raise

    # === Embedding methods ===

    async def _embed_documents(
        self,
        documents: list[Document],
    ) -> list[Document]:
        """Generate embeddings for documents.

        This method generates vector embeddings ONLY for document content,
        following the "content embedding" principle.

        Args:
            documents: List of documents without embeddings

        Returns:
            List of documents with embeddings
        """
        # Extract text content
        texts = [_extract_text_content(doc) for doc in documents]

        # Generate embeddings in batches
        response = await self.embedding_model(texts)

        # Attach embeddings to documents
        for doc, embedding in zip(documents, response.embeddings):
            doc.embedding = embedding

        return documents

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM and return text response.

        Args:
            prompt: Prompt text

        Returns:
            Response text content

        Raises:
            ValueError: If llm_model is None
        """
        if self.llm_model is None:
            raise ValueError("llm_model is required for this operation")

        # Call LLM with proper message format
        response = await self.llm_model(
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )

        # Extract text from response
        if response.content:
            text = response.content[0].get("text", "")
            return str(text) if text is not None else ""
        return ""

    async def _embed_query(self, query: str) -> list[float]:
        """Generate embedding for query string.

        Args:
            query: Query string

        Returns:
            Query embedding vector
        """
        response = await self.embedding_model([query])
        return response.embeddings[0]

    async def _embed_entities(self, entities: list[Entity]) -> list[Entity]:
        """Generate embeddings for entities.

        Following the "content embedding" principle, entity embeddings
        contain ONLY entity content (name + type + description), NOT
        relationship information.

        Args:
            entities: List of entities without embeddings

        Returns:
            List of entities with embeddings
        """
        if not self.entity_extraction_config.get(
            "generate_entity_embeddings",
            True,
        ):
            return entities

        # Create text representation: "name (type): description"
        texts = [
            f"{entity.name} ({entity.type}): {entity.description}"
            for entity in entities
        ]

        # Generate embeddings
        response = await self.embedding_model(texts)

        # Attach embeddings to entities
        for entity, embedding in zip(entities, response.embeddings):
            entity.embedding = embedding

        return entities

    # === Search methods ===

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
        score_threshold: float | None,
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
            logger.error(f"Vector search failed: {vector_results_raw}")
            vector_results = []
        else:
            vector_results = vector_results_raw

        if isinstance(graph_results_raw, Exception):
            logger.error(f"Graph search failed: {graph_results_raw}")
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
            doc.metadata.score = doc_scores[doc_id]  # Update with combined score
            results.append(doc)

        logger.debug(
            f"Hybrid search: {len(vector_results)} vector + "
            f"{len(graph_results)} graph = {len(results)} combined",
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
                - max_entities_per_community: Max entities to extract per community (default: 10)
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
        min_level = kwargs.get("min_community_level", 0)
        max_entities_per_comm = kwargs.get("max_entities_per_community", 10)
        community_limit = kwargs.get("community_limit", 5)

        # Step 1: Search for relevant communities
        communities = await self.graph_store.search_communities(
            query_embedding=query_embedding,
            min_level=min_level,
            limit=community_limit,
        )

        if not communities:
            logger.warning(
                "No communities found, falling back to vector search",
            )
            return await self._vector_search(query_embedding, limit, None)

        logger.debug(f"Found {len(communities)} relevant communities")

        # Step 2: Extract entity names from communities
        # Weight entities by community score
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

        if not entity_weights:
            logger.warning("No entities found in communities")
            return await self._vector_search(query_embedding, limit, None)

        entity_names = list(entity_weights.keys())
        logger.debug(
            f"Extracted {len(entity_names)} entities from communities",
        )

        # Step 3: Find documents mentioning these entities
        try:
            driver = self.graph_store.get_client()
            async with driver.session(
                database=self.graph_store.database,
            ) as session:
                query = f"""
                MATCH (e:Entity_{self.graph_store.collection_name})
                WHERE e.name IN $entity_names
                MATCH (e)<-[m:MENTIONS]-(doc:Document_{self.graph_store.collection_name})

                WITH doc,
                     count(DISTINCT e) AS entity_count,
                     sum(m.count) AS total_mentions,
                     collect(e.name) AS mentioned_entities,
                     gds.similarity.cosine(doc.embedding, $query_embedding) AS vector_similarity

                RETURN DISTINCT doc,
                       entity_count,
                       total_mentions,
                       mentioned_entities,
                       vector_similarity
                ORDER BY vector_similarity DESC, entity_count DESC, total_mentions DESC
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

                # Step 4: Calculate scores and convert to Document objects
                documents = []
                async for record in result:
                    node = record["doc"]
                    entity_count = record["entity_count"]
                    total_mentions = record["total_mentions"]
                    mentioned_entities = record["mentioned_entities"]
                    vector_similarity = record["vector_similarity"]

                    # Calculate entity weight contribution
                    entity_score_sum = sum(
                        entity_weights.get(name, 0.0) for name in mentioned_entities
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

                        # Weighted combination: vector similarity (60%) + community signals (40%)
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
                    f"Global search found {len(documents)} documents "
                    f"from {len(entity_names)} entities across {len(communities)} communities",
                )

                return documents

        except Exception as e:
            logger.error(f"Global search failed: {e}")
            raise GraphQueryError(f"Global search failed: {e}") from e

    # === Entity and relationship processing ===

    async def _process_entities_and_relationships(
        self,
        documents: list[Document],
    ) -> None:
        """Process entities and relationships for documents.

        This is the main entry point for entity/relationship extraction.
        It extracts entities, generates embeddings, extracts relationships,
        and stores everything in the graph database.

        Args:
            documents: List of documents with embeddings
        """
        logger.info(f"Extracting entities from {len(documents)} documents")

        # Step 1: Extract entities
        entities = await self._extract_entities(documents)

        if not entities:
            logger.warning("No entities extracted from documents")
            return

        logger.info(f"Extracted {len(entities)} entities")

        # Step 2: Generate entity embeddings
        entities_with_embeddings = await self._embed_entities(entities)

        # Step 3: Store entities in graph database
        for doc in documents:
            # Find entities extracted from this document
            doc_entities = [
                {
                    "name": entity.name,
                    "type": entity.type,
                    "description": entity.description,
                    "embedding": entity.embedding,
                }
                for entity in entities_with_embeddings
                # Note: We need to track which entities came from which document
                # For simplicity, we'll store all entities for now
            ]

            if doc_entities:
                await self.graph_store.add_entities(
                    entities=doc_entities,
                    document_id=doc.id,
                )

        # Step 4: Extract and store relationships if enabled
        if self.enable_relationship_extraction:
            logger.info("Extracting relationships between entities")
            relationships = await self._extract_relationships(
                documents,
                entities_with_embeddings,
            )

            if relationships:
                logger.info(f"Extracted {len(relationships)} relationships")
                relationships_data = [
                    {
                        "source": rel.source,
                        "target": rel.target,
                        "type": rel.type,
                        "description": rel.description,
                        "strength": rel.strength,
                    }
                    for rel in relationships
                ]
                await self.graph_store.add_relationships(relationships_data)

    # === Entity extraction ===

    async def _extract_entities(
        self,
        documents: list[Document],
    ) -> list[Entity]:
        """Extract entities from documents.

        This method supports single-pass extraction and optional gleanings
        (multi-round extraction to improve recall).

        Args:
            documents: List of documents

        Returns:
            List of extracted and resolved entities
        """
        # Single-pass extraction
        entities = await self._extract_entities_single_pass(documents)

        # Optional gleanings rounds
        if self.entity_extraction_config.get("enable_gleanings", False):
            gleanings_rounds = self.entity_extraction_config.get(
                "gleanings_rounds",
                2,
            )
            for round_num in range(gleanings_rounds):
                logger.info(
                    f"Gleanings round {round_num + 1}/{gleanings_rounds}",
                )
                new_entities = await self._gleanings_pass(documents, entities)
                entities.extend(new_entities)
                logger.info(f"Found {len(new_entities)} additional entities")

        # Resolve duplicates
        entities = self._resolve_entities(entities)

        return entities

    async def _extract_entities_single_pass(
        self,
        documents: list[Document],
    ) -> list[Entity]:
        """Extract entities in a single pass.

        Uses LLM to extract entities from each document chunk.

        Args:
            documents: List of documents

        Returns:
            List of extracted entities (may contain duplicates)
        """
        if not self.llm_model:
            return []

        # Extract entities from each document concurrently
        max_concurrent = 5  # Limit concurrent LLM calls
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_from_doc(doc: Document) -> list[Entity]:
            async with semaphore:
                return await self._extract_entities_from_text(
                    _extract_text_content(doc),
                    max_entities=self.entity_extraction_config.get(
                        "max_entities_per_chunk",
                        10,
                    ),
                )

        tasks = [extract_from_doc(doc) for doc in documents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect entities and handle exceptions
        all_entities: list[Entity] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Entity extraction failed for document {documents[i].id}: {result}",
                )
            elif isinstance(result, list):
                all_entities.extend(result)

        return all_entities

    async def _extract_entities_from_text(
        self,
        text: str,
        max_entities: int = 10,
    ) -> list[Entity]:
        """Extract entities from a single text using LLM.

        Args:
            text: Text content
            max_entities: Maximum number of entities to extract

        Returns:
            List of extracted entities
        """
        # Build prompt
        entity_types_str = ", ".join(
            self.entity_extraction_config.get("entity_types", []),
        )

        prompt = f"""Extract key entities from the following text. You must return ONLY a valid JSON array with no additional text, explanations, or formatting.

Text: {text}

Requirements:
1. Extract maximum {max_entities} most important entities
2. Each entity must have exactly these fields: "name", "type", "description"
3. Entity type must be one of: {entity_types_str}
4. Return ONLY the JSON array, no markdown, no code blocks, no explanations

Expected format:
[{{"name": "entity name", "type": "PERSON", "description": "brief description"}}]

JSON array:"""

        try:
            # Call LLM and get response text
            response_text = await self._call_llm(prompt)

            # Try to extract JSON from response
            # LLMs sometimes wrap JSON in markdown code blocks
            json_text = response_text.strip()
            if json_text.startswith("```json"):
                json_text = json_text[7:]  # Remove ```json
            if json_text.startswith("```"):
                json_text = json_text[3:]  # Remove ```
            if json_text.endswith("```"):
                json_text = json_text[:-3]  # Remove ```
            json_text = json_text.strip()

            # Parse JSON
            entity_data = json.loads(json_text)

            # Validate and create Entity objects using Pydantic
            entities = []
            for data in entity_data[:max_entities]:
                try:
                    entity = Entity(**data)
                    entities.append(entity)
                except Exception as e:
                    logger.warning(f"Invalid entity data: {data}, error: {e}")

            return entities

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return []
        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return []

    async def _gleanings_pass(
        self,
        documents: list[Document],
        existing_entities: list[Entity],
    ) -> list[Entity]:
        """Perform a gleanings pass to find missed entities.

        Gleanings is a technique to improve recall by asking the LLM
        to review the text again with knowledge of already-found entities.

        Args:
            documents: List of documents
            existing_entities: Entities found in previous passes

        Returns:
            List of newly discovered entities
        """
        if not self.llm_model:
            return []

        # Get existing entity names
        existing_names = {entity.name for entity in existing_entities}
        existing_names_str = ", ".join(
            list(existing_names)[:20],
        )  # Limit to 20 for prompt

        # Extract from each document
        max_concurrent = 5
        semaphore = asyncio.Semaphore(max_concurrent)

        async def glean_from_doc(doc: Document) -> list[Entity]:
            async with semaphore:
                prompt = f"""You already extracted these entities: {existing_names_str}

Review the text again and find any entities you might have missed. You must return ONLY a valid JSON array with no additional text.

Text: {_extract_text_content(doc)}

Requirements:
1. Return ONLY new entities not in the list above
2. Each entity must have exactly these fields: "name", "type", "description"
3. Return ONLY the JSON array, no markdown, no code blocks, no explanations
4. If no new entities, return []

Expected format:
[{{"name": "entity name", "type": "PERSON", "description": "brief description"}}]

JSON array:"""

                try:
                    response_text = await self._call_llm(prompt)
                    if not response_text:
                        response_text = "[]"

                    # Parse JSON
                    json_text = response_text.strip()
                    if json_text.startswith("```json"):
                        json_text = json_text[7:]
                    if json_text.startswith("```"):
                        json_text = json_text[3:]
                    if json_text.endswith("```"):
                        json_text = json_text[:-3]
                    json_text = json_text.strip()

                    entity_data = json.loads(json_text)

                    # Filter out existing entities and validate
                    new_entities = []
                    for data in entity_data:
                        if data.get("name") not in existing_names:
                            try:
                                entity = Entity(**data)
                                new_entities.append(entity)
                            except Exception as e:
                                logger.warning(
                                    f"Invalid entity data: {data}, error: {e}",
                                )

                    return new_entities

                except Exception as e:
                    logger.error(f"Gleanings extraction failed: {e}")
                    return []

        tasks = [glean_from_doc(doc) for doc in documents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect new entities
        new_entities: list[Entity] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Gleanings failed: {result}")
            elif isinstance(result, list):
                new_entities.extend(result)

        return new_entities

    def _resolve_entities(self, entities: list[Entity]) -> list[Entity]:
        """Resolve duplicate entities.

        This method deduplicates entities by name (case-insensitive).
        For duplicates, it keeps the entity with the longer description.

        Args:
            entities: List of entities (may contain duplicates)

        Returns:
            List of unique entities
        """
        entity_map: dict[str, Entity] = {}

        for entity in entities:
            name_lower = entity.name.lower()

            if name_lower not in entity_map:
                entity_map[name_lower] = entity
            else:
                # Keep the one with longer description
                existing = entity_map[name_lower]
                if len(entity.description) > len(existing.description):
                    entity_map[name_lower] = entity

        resolved = list(entity_map.values())
        logger.info(
            f"Resolved {len(entities)} entities to {len(resolved)} unique entities",
        )

        return resolved

    # === Relationship extraction ===

    async def _extract_relationships(
        self,
        documents: list[Document],
        entities: list[Entity],
    ) -> list[Relationship]:
        """Extract relationships between entities.

        Uses LLM to identify relationships between entities in the documents.

        Args:
            documents: List of documents
            entities: List of extracted entities

        Returns:
            List of relationships
        """
        if not self.llm_model or not entities:
            return []

        # Get entity names for prompt
        entity_names = [entity.name for entity in entities]
        entity_names_str = ", ".join(
            entity_names[:50],
        )  # Limit to 50 for prompt

        # Extract relationships from each document concurrently
        max_concurrent = 5
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_from_doc(doc: Document) -> list[Relationship]:
            async with semaphore:
                return await self._extract_relationships_from_text(
                    _extract_text_content(doc),
                    entity_names_str,
                )

        tasks = [extract_from_doc(doc) for doc in documents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect relationships
        all_relationships: list[Relationship] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Relationship extraction failed for document {documents[i].id}: {result}",
                )
            elif isinstance(result, list):
                all_relationships.extend(result)

        # Deduplicate relationships
        return self._deduplicate_relationships(all_relationships)

    async def _extract_relationships_from_text(
        self,
        text: str,
        entity_names: str,
    ) -> list[Relationship]:
        """Extract relationships from a single text using LLM.

        Args:
            text: Text content
            entity_names: Comma-separated list of entity names

        Returns:
            List of extracted relationships
        """
        prompt = f"""Extract relationships between entities in the text. You must return ONLY a valid JSON array with no additional text.

Text: {text}

Known entities: {entity_names}

Requirements:
1. Extract relationships between entities mentioned in the Known entities list
2. Each relationship must have exactly these fields: "source", "target", "type", "description"
3. Relationship type should be clear (e.g., WORKS_FOR, LOCATED_IN, CREATED, COLLABORATES_WITH)
4. Return ONLY the JSON array, no markdown, no code blocks, no explanations
5. If no relationships, return []

Expected format:
[{{"source": "entity1", "target": "entity2", "type": "WORKS_FOR", "description": "entity1 works for entity2"}}]

JSON array:"""

        try:
            response_text = await self._call_llm(prompt)
            if not response_text:
                response_text = "[]"

            # Parse JSON
            json_text = response_text.strip()
            if json_text.startswith("```json"):
                json_text = json_text[7:]
            if json_text.startswith("```"):
                json_text = json_text[3:]
            if json_text.endswith("```"):
                json_text = json_text[:-3]
            json_text = json_text.strip()

            rel_data = json.loads(json_text)

            # Validate and create Relationship objects using Pydantic
            relationships = []
            for data in rel_data:
                try:
                    relationship = Relationship(**data)
                    relationships.append(relationship)
                except Exception as e:
                    logger.warning(
                        f"Invalid relationship data: {data}, error: {e}",
                    )

            return relationships

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return []
        except Exception as e:
            logger.error(f"Relationship extraction failed: {e}")
            return []

    def _deduplicate_relationships(
        self,
        relationships: list[Relationship],
    ) -> list[Relationship]:
        """Deduplicate relationships.

        Relationships are considered duplicates if they have the same
        source, target, and type (order matters).

        Args:
            relationships: List of relationships (may contain duplicates)

        Returns:
            List of unique relationships
        """
        rel_map: dict[tuple, Relationship] = {}

        for rel in relationships:
            key = (rel.source.lower(), rel.target.lower(), rel.type)

            if key not in rel_map:
                rel_map[key] = rel
            else:
                # Keep the one with longer description
                existing = rel_map[key]
                if len(rel.description) > len(existing.description):
                    rel_map[key] = rel

        deduplicated = list(rel_map.values())
        logger.info(
            f"Deduplicated {len(relationships)} relationships to "
            f"{len(deduplicated)} unique relationships",
        )

        return deduplicated

    # === Community detection ===

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
        logger.info(f"Starting community detection using {algo} algorithm")

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

            logger.info(f"Detected {len(communities_raw)} raw communities")

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
            levels = max(comm.level for comm in communities_with_embeddings) + 1

            logger.info(
                f"Community detection completed: {len(communities_with_embeddings)} "
                f"communities across {levels} levels",
            )

            return {
                "community_count": len(communities_with_embeddings),
                "levels": levels,
                "algorithm": algo,
                "status": "success",
            }

        except Exception as e:
            logger.error(f"Community detection failed: {e}")
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
            **kwargs: Additional algorithm parameters

        Returns:
            List of raw communities (without summaries or embeddings)
        """
        driver = self.graph_store.get_client()

        try:
            async with driver.session(
                database=self.graph_store.database,
            ) as session:
                # Step 1: Create graph projection
                projection_name = f"entity_graph_{self.graph_store.collection_name}"

                # Drop existing projection if any
                try:
                    await session.run(
                        f"CALL gds.graph.drop('{projection_name}', false)",
                    )
                except Exception:
                    pass  # Projection doesn't exist, that's fine

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
                    f"Algorithm stats: {stats['communityCount']} communities detected",
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
                    # Handle both single value and list (from includeIntermediateCommunities)
                    if isinstance(comm_id, list):
                        comm_id = comm_id[-1]  # Use the highest level community
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
                    # Generate a simple placeholder summary to satisfy Pydantic validation
                    placeholder_summary = f"Community of {len(comm_data['entity_names'])} entities: {', '.join(comm_data['entity_names'][:3])}"
                    if len(comm_data["entity_names"]) > 3:
                        placeholder_summary += ", ..."

                    community = Community(
                        id=comm_data["id"],
                        level=comm_data["level"],
                        title=f"Community {comm_data['id']}",  # Placeholder
                        summary=placeholder_summary,  # Temporary, will be replaced by LLM
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
            logger.error(f"GDS community detection failed: {e}")
            # Try to clean up projection on error
            try:
                await session.run(
                    f"CALL gds.graph.drop('{projection_name}', false)",
                )
            except Exception:
                pass
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
            return self._generate_simple_summaries(communities)

        logger.info(f"Generating summaries for {len(communities)} communities")

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
                    f"Summary generation failed for {communities[i].id}: {result}",
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

        prompt = f"""Summarize the following group of entities into a cohesive theme. You must return ONLY a valid JSON object with no additional text.

Entities: {entity_names_str}

Entity descriptions: {entity_descs_str}

Requirements:
1. Provide a brief summary (2-3 sentences) describing what these entities have in common
2. Provide a short title (3-5 words) for this community
3. The JSON must have exactly these fields: "title", "summary"
4. Return ONLY the JSON object, no markdown, no code blocks, no explanations

Expected format:
{{"title": "short title", "summary": "2-3 sentence summary describing the common theme"}}

JSON object:"""

        try:
            response_text = await self._call_llm(prompt)
            if not response_text:
                response_text = "{}"

            # Parse JSON
            json_text = response_text.strip()
            if json_text.startswith("```json"):
                json_text = json_text[7:]
            if json_text.startswith("```"):
                json_text = json_text[3:]
            if json_text.endswith("```"):
                json_text = json_text[:-3]
            json_text = json_text.strip()

            data = json.loads(json_text)

            community.title = data.get("title", f"Community {community.id}")
            summary = data.get("summary", "").strip()

            # Ensure summary is not empty (Pydantic validation requirement)
            if not summary:
                summary = (
                    f"A community containing: {', '.join(community.entity_ids[:5])}"
                )

            community.summary = summary

            # Calculate rating based on entity count (simple heuristic)
            community.rating = min(1.0, community.entity_count / 10.0)

            return community

        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            # Fallback to simple summary
            community.title = f"Group of {community.entity_count} entities"
            community.summary = (
                f"A community containing: {', '.join(community.entity_ids[:5])}"
            )
            community.rating = 0.5
            return community

    def _generate_simple_summaries(
        self,
        communities: list[Community],
    ) -> list[Community]:
        """Generate simple rule-based summaries (fallback).

        Args:
            communities: List of communities

        Returns:
            List of communities with simple summaries
        """
        for comm in communities:
            comm.title = f"Group of {comm.entity_count} entities"
            comm.summary = f"A community containing: {', '.join(comm.entity_ids[:5])}"
            comm.rating = 0.5
        return communities

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
            f"Generating embeddings for {len(communities)} communities",
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
            f"Successfully generated embeddings for {embeddings_ok}/{len(communities)} communities",
        )

        return communities
