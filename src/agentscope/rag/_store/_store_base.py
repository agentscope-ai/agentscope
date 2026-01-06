# -*- coding: utf-8 -*-
"""Store base classes for knowledge base storage.

This module provides a layered abstraction for different storage types:
- StoreBase: Top-level abstraction for all storage types
- VDBStoreBase: Vector database storage abstraction
- GraphStoreBase: Graph database storage abstraction
"""
from abc import ABC, abstractmethod
from typing import Any

from .. import Document
from ...types import Embedding


class StoreBase(ABC):
    """Top-level base class for all storage types.

    This abstract class defines the common interface that all storage
    implementations (vector databases, graph databases, etc.) must implement.

    The core methods are:
    - add(): Add documents to storage
    - delete(): Delete documents from storage
    - search(): Search for relevant documents based on query embedding
    - get_client(): Access the underlying storage client
    """

    @abstractmethod
    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add documents to the storage.

        Args:
            documents: List of documents to add
            **kwargs: Additional storage-specific arguments
        """

    @abstractmethod
    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete documents from the storage.

        Args:
            *args: Positional arguments for deletion (storage-specific)
            **kwargs: Keyword arguments for deletion (storage-specific)
        """

    @abstractmethod
    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search for relevant documents based on query embedding.

        All storage types must support vector similarity search as the
        fundamental retrieval mechanism.

        Args:
            query_embedding: The embedding vector of the query
            limit: Maximum number of documents to retrieve
            score_threshold: Minimum similarity score threshold (optional)
            **kwargs: Additional storage-specific search arguments

        Returns:
            List of relevant documents sorted by relevance
        """

    def get_client(self) -> Any:
        """Get the underlying storage client.

        This allows developers to access the full functionality of the
        underlying storage system beyond the abstracted interface.

        Returns:
            The underlying storage client object

        Raises:
            NotImplementedError: If the storage implementation doesn't
                                 support direct client access
        """
        raise NotImplementedError(
            f"``get_client`` is not implemented for "
            f"{self.__class__.__name__}.",
        )


class VDBStoreBase(StoreBase):
    """Vector database store base class.

    This class serves as an abstraction layer for vector database
    implementations (e.g., Qdrant, Milvus, Weaviate).

    Inherits all methods from StoreBase without adding additional
    abstract methods, maintaining backward compatibility with existing
    vector database implementations.

    Existing implementations (QdrantStore, MilvusLiteStore) should
    continue to work without any modifications.
    """


class GraphStoreBase(StoreBase):
    """Graph database store base class.

    This class extends StoreBase with graph-specific operations such as
    entity management, relationship management, and graph traversal.

    Suitable for graph databases like Neo4j, TigerGraph, JanusGraph, etc.
    """

    # === Graph-specific abstract methods ===

    @abstractmethod
    async def add_entities(
        self,
        entities: list[dict],
        document_id: str,
        **kwargs: Any,
    ) -> None:
        """Add entity nodes and link them to a document.

        Args:
            entities: List of entity dictionaries with structure:
                     [{"name": str, "type": str, "description": str}, ...]
            document_id: ID of the document these entities are extracted from
            **kwargs: Additional graph-specific arguments
        """

    @abstractmethod
    async def add_relationships(
        self,
        relationships: list[dict],
        **kwargs: Any,
    ) -> None:
        """Add relationships between entities.

        Args:
            relationships: List of relationship dictionaries with structure:
                          [{"source": str, "target": str, "type": str,
                            "description": str, "strength": float}, ...]
            **kwargs: Additional graph-specific arguments
        """

    @abstractmethod
    async def search_entities(
        self,
        query_embedding: Embedding,
        limit: int,
        **kwargs: Any,
    ) -> list[dict]:
        """Search for entities using vector similarity.

        Args:
            query_embedding: The embedding vector of the query
            limit: Maximum number of entities to retrieve
            **kwargs: Additional search arguments

        Returns:
            List of entity dictionaries
        """

    @abstractmethod
    async def search_with_graph(
        self,
        query_embedding: Embedding,
        max_hops: int = 2,
        limit: int = 5,
        **kwargs: Any,
    ) -> list[Document]:
        """Graph traversal-based search.

        This method combines vector search with graph traversal to find
        documents that are structurally related to the query.

        Process:
        1. Vector search to find seed entities
        2. Graph traversal to find related entities (N hops)
        3. Collect documents that mention these entities

        Args:
            query_embedding: The embedding vector of the query
            max_hops: Maximum number of hops for graph traversal
            limit: Maximum number of documents to retrieve
            **kwargs: Additional search arguments

        Returns:
            List of relevant documents
        """

    # === Optional community detection methods ===

    async def add_communities(
        self,
        communities: list[dict],
        **kwargs: Any,
    ) -> None:
        """Add community nodes (optional feature).

        This method is optional and raises NotImplementedError by default.
        Subclasses can choose to implement community detection functionality.

        Args:
            communities: List of community dictionaries with structure:
                        [{"id": str, "level": int, "title": str,
                          "summary": str, "rating": float,
                          "entity_count": int, "entity_ids": list}, ...]
            **kwargs: Additional graph-specific arguments

        Raises:
            NotImplementedError: If community detection is not supported
        """
        raise NotImplementedError(
            f"Community detection is not supported in "
            f"{self.__class__.__name__}.",
        )

    async def search_communities(
        self,
        query_embedding: Embedding,
        min_level: int = 1,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[dict]:
        """Search for relevant communities (optional feature).

        This method is optional and raises NotImplementedError by default.

        Args:
            query_embedding: The embedding vector of the query
            min_level: Minimum community hierarchical level
            limit: Maximum number of communities to retrieve
            **kwargs: Additional search arguments

        Returns:
            List of community dictionaries

        Raises:
            NotImplementedError: If community search is not supported
        """
        raise NotImplementedError(
            f"Community search is not supported in "
            f"{self.__class__.__name__}.",
        )
