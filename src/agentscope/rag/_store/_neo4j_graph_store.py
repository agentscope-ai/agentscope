# -*- coding: utf-8 -*-
"""Neo4j graph database store implementation."""

import asyncio
from typing import Any

from ..._logging import logger
from ._store_base import GraphStoreBase
from .. import Document, DocMetadata
from ...types import Embedding
from ...exception import DatabaseConnectionError, GraphQueryError


class Neo4jGraphStore(GraphStoreBase):
    """Neo4j graph database store implementation.

    This class implements the GraphStoreBase interface using Neo4j as
    the backend graph database. It supports:

    - Document storage with vector embeddings
    - Entity extraction and storage
    - Relationship management
    - Graph traversal-based retrieval
    - Community detection (optional)

    Attributes:
        uri: Neo4j connection URI (e.g., bolt://localhost:7687)
        user: Neo4j username
        password: Neo4j password
        database: Neo4j database name
        collection_name: Collection name (used as node label prefix)
        dimensions: Vector embedding dimensions
        driver: Neo4j async driver instance

    Example:
        >>> store = Neo4jGraphStore(
        ...     uri="bolt://localhost:7687",
        ...     user="neo4j",
        ...     password="password",
        ...     database="neo4j",
        ...     collection_name="knowledge",
        ...     dimensions=1536,
        ... )
        >>> await store.add(documents)
        >>> results = await store.search(query_embedding, limit=5)
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
        database: str = "neo4j",
        collection_name: str = "knowledge",
        dimensions: int = 1536,
    ) -> None:
        """Initialize Neo4j graph store.

        Args:
            uri: Neo4j connection URI
            user: Neo4j username
            password: Neo4j password
            database: Neo4j database name
            collection_name: Collection name prefix for node labels
            dimensions: Vector embedding dimensions (default: 1536 for OpenAI)

        Raises:
            DatabaseConnectionError: If connection to Neo4j fails after retries
        """
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError as e:
            raise ImportError(
                "neo4j package is required for Neo4jGraphStore. "
                "Please install it with: pip install neo4j",
            ) from e

        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self.collection_name = collection_name
        self.dimensions = dimensions

        # Initialize Neo4j driver
        self.driver = AsyncGraphDatabase.driver(
            uri,
            auth=(user, password),
        )

        # Ensure connection is established (with retry mechanism)
        asyncio.create_task(self._initialize())

    async def _initialize(self) -> None:
        """Initialize connection and ensure indexes exist."""
        await self._ensure_connection()
        await self._ensure_indexes()

    async def _ensure_connection(self) -> None:
        """Ensure Neo4j connection is established (with retry mechanism).

        This method attempts to connect to Neo4j up to 3 times with
        exponential backoff. If all attempts fail, it raises a
        DatabaseConnectionError.

        Raises:
            DatabaseConnectionError: If connection fails after all retries
        """
        from neo4j.exceptions import ServiceUnavailable, AuthError

        max_retries = 3
        retry_delay = 1  # Initial delay in seconds

        for attempt in range(max_retries):
            try:
                # Execute a simple query to verify connection
                async with self.driver.session(
                    database=self.database,
                ) as session:
                    result = await session.run("RETURN 1")
                    await result.single()

                logger.info(
                    "Neo4j connection established: %s (database: %s)",
                    self.uri,
                    self.database,
                )
                return

            except (ServiceUnavailable, AuthError) as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Connection attempt %s failed: %s, retrying in %ss...",
                        attempt + 1,
                        e,
                        retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error(
                        "Failed to connect to Neo4j after %s attempts",
                        max_retries,
                    )
                    raise DatabaseConnectionError(
                        f"Cannot connect to Neo4j at {self.uri}: {e}",
                    ) from e

    async def _ensure_indexes(self) -> None:
        """Ensure required vector indexes exist in Neo4j.

        This method creates vector indexes for documents, entities, and
        communities if they don't already exist. It requires Neo4j 6.0.2+
        with vector index support.

        Raises:
            GraphQueryError: If index creation fails
        """
        try:
            async with self.driver.session(database=self.database) as session:
                # Create document vector index (unique per collection)
                document_index_query = f"""
                CREATE VECTOR INDEX document_vector_idx_{
                    self.collection_name
                } IF NOT EXISTS
                FOR (d:Document_{self.collection_name})
                ON d.embedding
                OPTIONS {{
                    indexConfig: {{
                        `vector.dimensions`: {self.dimensions},
                        `vector.similarity_function`: 'cosine',
                        `vector.quantization.enabled`: false
                    }}
                }}
                """
                await session.run(document_index_query)

                # Create entity vector index (optional, for entity
                # search)
                entity_index_query = f"""
                CREATE VECTOR INDEX entity_vector_idx_{
                    self.collection_name
                } IF NOT EXISTS
                FOR (e:Entity_{self.collection_name})
                ON e.embedding
                OPTIONS {{
                    indexConfig: {{
                        `vector.dimensions`: {self.dimensions},
                        `vector.similarity_function`: 'cosine',
                        `vector.quantization.enabled`: false
                    }}
                }}
                """
                await session.run(entity_index_query)

                community_index_query = f"""
                CREATE VECTOR INDEX community_vector_idx_{
                    self.collection_name
                } IF NOT EXISTS
                FOR (c:Community_{self.collection_name})
                ON c.embedding
                OPTIONS {{
                    indexConfig: {{
                        `vector.dimensions`: {self.dimensions},
                        `vector.similarity_function`: 'cosine',
                        `vector.quantization.enabled`: false
                    }}
                }}
                """
                await session.run(community_index_query)

                logger.info(
                    "Vector indexes ensured for collection: %s",
                    self.collection_name,
                )

        except Exception as e:
            logger.error("Failed to create vector indexes: %s", e)
            raise GraphQueryError(
                f"Failed to create vector indexes: {e}",
            ) from e

    # === StoreBase implementation ===

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add documents to Neo4j (implements StoreBase.add).

        This method stores document nodes with their vector embeddings.
        Each document becomes a node with properties:
        - id: Document ID
        - content: Document text content
        - embedding: Vector embedding
        - doc_id: Original document ID
        - chunk_id: Chunk identifier
        - total_chunks: Total number of chunks
        - created_at: Timestamp

        Args:
            documents: List of documents to add
            **kwargs: Additional arguments (unused)

        Raises:
            GraphQueryError: If document addition fails
        """
        if not documents:
            return

        try:
            async with self.driver.session(database=self.database) as session:
                # Prepare document data for batch insertion
                doc_data = [
                    {
                        "id": doc.id,
                        "content": doc.get_text(),
                        "embedding": doc.embedding,
                        "doc_id": doc.metadata.doc_id,
                        "chunk_id": doc.metadata.chunk_id,
                        "total_chunks": doc.metadata.total_chunks,
                    }
                    for doc in documents
                ]

                query = f"""
                UNWIND $docs AS doc
                MERGE (d:Document_{self.collection_name} {{id: doc.id}})
                SET d.content = doc.content,
                    d.embedding = doc.embedding,
                    d.doc_id = doc.doc_id,
                    d.chunk_id = doc.chunk_id,
                    d.total_chunks = doc.total_chunks,
                    d.created_at = datetime()
                """

                await session.run(query, {"docs": doc_data})

                logger.info(
                    "Added %s documents to Neo4j (collection: %s)",
                    len(documents),
                    self.collection_name,
                )

        except Exception as e:
            logger.error("Failed to add documents: %s", e)
            raise GraphQueryError(f"Failed to add documents: {e}") from e

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete documents from Neo4j (implements StoreBase.delete).

        Args:
            *args: Can be document IDs (str) or a list of IDs
            **kwargs: Additional arguments (e.g., doc_id, chunk_id)

        Raises:
            GraphQueryError: If deletion fails
        """
        try:
            async with self.driver.session(database=self.database) as session:
                # Handle different deletion patterns
                if args and isinstance(args[0], (list, tuple)):
                    # Delete by list of IDs
                    doc_ids = args[0]
                    query = f"""
                    MATCH (d:Document_{self.collection_name})
                    WHERE d.id IN $doc_ids
                    DETACH DELETE d
                    """
                    await session.run(query, {"doc_ids": doc_ids})
                    logger.info("Deleted %s documents by IDs", len(doc_ids))

                elif "doc_id" in kwargs:
                    # Delete by doc_id (all chunks)
                    query = f"""
                    MATCH (d:Document_{self.collection_name})
                    WHERE d.doc_id = $doc_id
                    DETACH DELETE d
                    """
                    await session.run(query, {"doc_id": kwargs["doc_id"]})
                    logger.info(
                        "Deleted documents with doc_id: %s",
                        kwargs["doc_id"],
                    )

                else:
                    logger.warning("No valid deletion criteria provided")

        except Exception as e:
            logger.error("Failed to delete documents: %s", e)
            raise GraphQueryError(f"Failed to delete documents: {e}") from e

    async def search(
        self,
        query_embedding: Embedding,
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Vector search for documents (implements StoreBase.search).

        This method performs pure vector similarity search on document nodes.

        Args:
            query_embedding: Query embedding vector
            limit: Maximum number of documents to return
            score_threshold: Minimum similarity score threshold
            **kwargs: Additional search arguments (unused)

        Returns:
            List of relevant documents sorted by similarity score

        Raises:
            GraphQueryError: If search fails
        """
        try:
            async with self.driver.session(database=self.database) as session:
                # Vector similarity search using Neo4j's vector index
                index_name = f"document_vector_idx_{self.collection_name}"
                query = f"""
                CALL db.index.vector.queryNodes(
                    '{index_name}',
                    $limit,
                    $query_embedding
                )
                YIELD node, score
                WHERE score >= $score_threshold
                RETURN node, score
                ORDER BY score DESC
                """

                result = await session.run(
                    query,
                    {
                        "query_embedding": query_embedding,
                        "limit": limit,
                        "score_threshold": score_threshold or 0.0,
                    },
                )

                # Convert Neo4j nodes to Document objects
                documents = []
                async for record in result:
                    node = record["node"]
                    score = record["score"]

                    doc = Document(
                        id=node["id"],
                        embedding=node["embedding"],
                        metadata=DocMetadata(
                            content={"type": "text", "text": node["content"]},
                            doc_id=node["doc_id"],
                            chunk_id=node["chunk_id"],
                            total_chunks=node["total_chunks"],
                        ),
                        score=score,
                    )
                    documents.append(doc)

                logger.debug(
                    "Vector search found %s documents (limit: %s, "
                    "threshold: %s)",
                    len(documents),
                    limit,
                    score_threshold,
                )

                return documents

        except Exception as e:
            logger.error("Vector search failed: %s", e)
            raise GraphQueryError(f"Vector search failed: {e}") from e

    def get_client(self) -> "AsyncDriver":
        """Get Neo4j driver (implements StoreBase.get_client).

        Returns:
            Neo4j async driver instance
        """
        return self.driver

    async def __aenter__(self) -> "Neo4jGraphStore":
        """Async context manager entry.

        Returns:
            Self for use in async with statements
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit.

        Ensures the Neo4j driver connection is properly closed.

        Args:
            exc_type: Exception type if an exception was raised
            exc_val: Exception value if an exception was raised
            exc_tb: Exception traceback if an exception was raised
        """
        await self.close()

    # === GraphStoreBase implementation ===

    async def add_entities(
        self,
        entities: list[dict],
        document_id: str,
        **kwargs: Any,
    ) -> None:
        """Add entity nodes and link to document (implements
        GraphStoreBase.add_entities).

        This method creates entity nodes and MENTIONS relationships from
        the document to the entities.

        Args:
            entities: List of entity dicts with keys: name, type,
                description, embedding
            document_id: ID of the document these entities are from
            **kwargs: Additional arguments (unused)

        Raises:
            GraphQueryError: If entity addition fails
        """
        if not entities:
            return

        try:
            async with self.driver.session(database=self.database) as session:
                # Batch insert entities and create relationships
                query = f"""
                UNWIND $entities AS entity
                MERGE (e:Entity_{self.collection_name} {{name: entity.name}})
                SET e.type = entity.type,
                    e.description = entity.description,
                    e.embedding = entity.embedding,
                    e.updated_at = datetime()

                WITH e, entity
                MATCH (d:Document_{self.collection_name} {{id: $document_id}})
                MERGE (d)-[r:MENTIONS]->(e)
                ON CREATE SET r.count = 1
                ON MATCH SET r.count = r.count + 1
                """

                await session.run(
                    query,
                    {"entities": entities, "document_id": document_id},
                )

                logger.info(
                    "Added %s entities for document %s",
                    len(entities),
                    document_id,
                )

        except Exception as e:
            logger.error("Failed to add entities: %s", e)
            raise GraphQueryError(f"Failed to add entities: {e}") from e

    async def add_relationships(
        self,
        relationships: list[dict],
        **kwargs: Any,
    ) -> None:
        """Add relationships between entities (implements
        GraphStoreBase.add_relationships).

        Args:
            relationships: List of relationship dicts with keys:
                          source, target, type, description, strength
            **kwargs: Additional arguments (unused)

        Raises:
            GraphQueryError: If relationship addition fails
        """
        if not relationships:
            return

        try:
            async with self.driver.session(database=self.database) as session:
                # Batch create relationships
                query = f"""
                UNWIND $relationships AS rel
                MATCH (source:Entity_{self.collection_name} {{\
name: rel.source}})
                MATCH (target:Entity_{self.collection_name} {{\
name: rel.target}})
                MERGE (source)-[r:RELATED_TO {{type: rel.type}}]->\
(target)
                SET r.description = rel.description,
                    r.strength = rel.strength,
                    r.updated_at = datetime()
                """

                await session.run(query, {"relationships": relationships})

                logger.info("Added %s relationships", len(relationships))

        except Exception as e:
            logger.error("Failed to add relationships: %s", e)
            raise GraphQueryError(f"Failed to add relationships: {e}") from e

    async def search_entities(
        self,
        query_embedding: Embedding,
        limit: int = 5,
        **kwargs: Any,
    ) -> list[dict]:
        """Vector search for entities (implements
        GraphStoreBase.search_entities).

        Args:
            query_embedding: Query embedding vector
            limit: Maximum number of entities to return
            **kwargs: Additional search arguments (unused)

        Returns:
            List of entity dictionaries

        Raises:
            GraphQueryError: If entity search fails
        """
        try:
            async with self.driver.session(database=self.database) as session:
                index_name = f"entity_vector_idx_{self.collection_name}"
                query = f"""
                CALL db.index.vector.queryNodes(
                    '{index_name}',
                    $limit,
                    $query_embedding
                )
                YIELD node, score
                RETURN node.name AS name,
                       node.type AS type,
                       node.description AS description,
                       score
                ORDER BY score DESC
                """

                result = await session.run(
                    query,
                    {"query_embedding": query_embedding, "limit": limit},
                )

                entities = []
                async for record in result:
                    entities.append(
                        {
                            "name": record["name"],
                            "type": record["type"],
                            "description": record["description"],
                            "score": record["score"],
                        },
                    )

                logger.debug("Entity search found %s entities", len(entities))
                return entities

        except Exception as e:
            logger.error("Entity search failed: %s", e)
            raise GraphQueryError(f"Entity search failed: {e}") from e

    async def search_with_graph(
        self,
        query_embedding: Embedding,
        max_hops: int = 2,
        limit: int = 5,
        **kwargs: Any,
    ) -> list[Document]:
        """Graph traversal-based search (implements
        GraphStoreBase.search_with_graph).

        Process:
        1. Vector search to find seed entities
        2. Graph traversal to find related entities (N hops)
        3. Collect documents that mention these entities

        Args:
            query_embedding: Query embedding vector
            max_hops: Maximum number of hops for graph traversal
            limit: Maximum number of documents to return
            **kwargs: Additional search arguments

        Returns:
            List of relevant documents

        Raises:
            GraphQueryError: If graph search fails
        """
        try:
            async with self.driver.session(database=self.database) as session:
                # Combined query: vector search + graph traversal +
                # document collection
                index_name = f"entity_vector_idx_{self.collection_name}"
                query = f"""
                // Step 1: Vector search for seed entities
                CALL db.index.vector.queryNodes(
                    '{index_name}',
                    5,
                    $query_embedding
                )
                YIELD node AS seed_entity

                // Step 2: Graph traversal to find related entities
                MATCH path = (seed_entity)-[:RELATED_TO*1..{max_hops}]-(
                    related_entity
                )

                // Step 3: Collect documents mentioning these entities
                MATCH (related_entity)<-[:MENTIONS]-(doc:Document_{
                    self.collection_name
                })

                RETURN DISTINCT doc,
                       length(path) AS hops,
                       seed_entity.name AS seed_name
                ORDER BY hops ASC
                LIMIT $limit
                """

                result = await session.run(
                    query,
                    {"query_embedding": query_embedding, "limit": limit},
                )

                documents = []
                async for record in result:
                    node = record["doc"]
                    hops = record["hops"]

                    doc = Document(
                        id=node["id"],
                        embedding=node["embedding"],
                        metadata=DocMetadata(
                            content={"type": "text", "text": node["content"]},
                            doc_id=node["doc_id"],
                            chunk_id=node["chunk_id"],
                            total_chunks=node["total_chunks"],
                        ),
                        score=1.0 / (hops + 1),  # Score decreases with hops
                    )
                    documents.append(doc)

                logger.debug(
                    "Graph search found %s documents (max_hops: %s)",
                    len(documents),
                    max_hops,
                )

                return documents

        except Exception as e:
            logger.error("Graph search failed: %s", e)
            raise GraphQueryError(f"Graph search failed: {e}") from e

    # === Optional community detection methods ===

    async def add_communities(
        self,
        communities: list[dict],
        **kwargs: Any,
    ) -> None:
        """Add community nodes (optional, implements
        GraphStoreBase.add_communities).

        Args:
            communities: List of community dicts with keys:
                        id, level, title, summary, rating, entity_count,
                        entity_ids, embedding
            **kwargs: Additional arguments (unused)

        Raises:
            GraphQueryError: If community addition fails
        """
        if not communities:
            return

        try:
            async with self.driver.session(database=self.database) as session:
                # Batch insert communities
                query = f"""
                UNWIND $communities AS comm
                MERGE (c:Community_{self.collection_name} {{id: comm.id}})
                SET c.level = comm.level,
                    c.title = comm.title,
                    c.summary = comm.summary,
                    c.rating = comm.rating,
                    c.entity_count = comm.entity_count,
                    c.embedding = comm.embedding,
                    c.updated_at = datetime()

                WITH c, comm
                UNWIND comm.entity_ids AS entity_name
                MATCH (e:Entity_{self.collection_name} {{name: entity_name}})
                MERGE (e)-[:BELONGS_TO]->(c)
                """

                await session.run(query, {"communities": communities})

                logger.info(
                    "Added %s communities to database",
                    len(communities),
                )

        except Exception as e:
            logger.error("Failed to add communities: %s", e)
            raise GraphQueryError(f"Failed to add communities: {e}") from e

    async def search_communities(
        self,
        query_embedding: Embedding,
        min_level: int = 1,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[dict]:
        """Search for communities (optional, implements
        GraphStoreBase.search_communities).

        Args:
            query_embedding: Query embedding vector
            min_level: Minimum community hierarchical level
            limit: Maximum number of communities to return
            **kwargs: Additional search arguments

        Returns:
            List of community dictionaries with entity_ids

        Raises:
            GraphQueryError: If community search fails
        """
        try:
            async with self.driver.session(database=self.database) as session:
                # Perform vector search using collection-specific
                # index
                index_name = f"community_vector_idx_{self.collection_name}"
                query = f"""
                CALL db.index.vector.queryNodes(
                    '{index_name}',
                    $limit,
                    $query_embedding
                )
                YIELD node AS community, score
                WHERE community.level >= $min_level

                // Get entities belonging to this community
                OPTIONAL MATCH (entity:Entity_{self.collection_name})-\
[:BELONGS_TO]->(community)

                WITH community, score, collect(entity.name) AS entity_names

                RETURN community.id AS id,
                       community.level AS level,
                       community.title AS title,
                       community.summary AS summary,
                       community.rating AS rating,
                       community.entity_count AS entity_count,
                       entity_names AS entity_ids,
                       score
                ORDER BY score DESC
                """

                result = await session.run(
                    query,
                    {
                        "query_embedding": query_embedding,
                        "min_level": min_level,
                        "limit": limit,
                    },
                )

                communities = []
                async for record in result:
                    entity_ids = record["entity_ids"] or []
                    communities.append(
                        {
                            "id": record["id"],
                            "level": record["level"],
                            "title": record["title"],
                            "summary": record["summary"],
                            "rating": record["rating"],
                            "entity_count": record["entity_count"],
                            "entity_ids": entity_ids,  # Now includes
                            # entity IDs!
                            "score": record["score"],
                        },
                    )

                logger.info(
                    "Community search returned %s communities with %s "
                    "total entities",
                    len(communities),
                    sum(len(c["entity_ids"]) for c in communities),
                )
                return communities

        except Exception as e:
            logger.error("Community search failed: %s", e)
            raise GraphQueryError(f"Community search failed: {e}") from e

    async def close(self) -> None:
        """Close the Neo4j driver connection."""
        if self.driver:
            await self.driver.close()
            logger.info("Neo4j connection closed")
