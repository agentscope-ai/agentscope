# -*- coding: utf-8 -*-
"""RAG-specific exceptions in agentscope."""

from ._exception_base import AgentOrientedExceptionBase


class RAGExceptionBase(AgentOrientedExceptionBase):
    """Base class for all RAG-related exceptions.

    This is the base exception class for all RAG module errors,
    including graph database operations, entity extraction, and
    knowledge retrieval failures.
    """


class DatabaseConnectionError(RAGExceptionBase):
    """Exception raised when database connection fails.

    This exception is raised when:
    - Unable to connect to Neo4j database
    - Connection health check fails
    - Connection is lost during operation

    Args:
        message: Detailed error message describing the connection failure

    Example:
        >>> raise DatabaseConnectionError(
        ...     "Cannot connect to Neo4j at bolt://localhost:7687: "
        ...     "Connection refused"
        ... )
    """


class GraphQueryError(RAGExceptionBase):
    """Exception raised when graph query execution fails.

    This exception is raised when:
    - Cypher query syntax error
    - Query execution timeout
    - Query returns unexpected results
    - Transaction rollback failure

    Args:
        message: Detailed error message describing the query failure

    Example:
        >>> raise GraphQueryError(
        ...     "Failed to execute Cypher query: "
        ...     "Node with ID 'doc1' not found"
        ... )
    """


class EntityExtractionError(RAGExceptionBase):
    """Exception raised when entity extraction fails.

    This exception is raised when:
    - LLM fails to extract entities from text
    - Entity data validation fails
    - Entity resolution fails

    Args:
        message: Detailed error message describing the extraction failure

    Example:
        >>> raise EntityExtractionError(
        ...     "Failed to extract entities from document: "
        ...     "LLM returned invalid JSON format"
        ... )
    """


class IndexNotFoundError(RAGExceptionBase):
    """Exception raised when required vector index does not exist.

    This exception is raised when:
    - Required vector index is not found in Neo4j
    - Index creation fails
    - Index is in invalid state

    Args:
        message: Detailed error message describing the index issue

    Example:
        >>> raise IndexNotFoundError(
        ...     "Vector index 'document_vector_idx' not found. "
        ...     "Please ensure Neo4j version is 6.0.2+"
        ... )
    """
