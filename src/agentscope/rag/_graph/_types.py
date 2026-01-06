# -*- coding: utf-8 -*-
"""Data types and models for graph-based knowledge representation.

This module provides Pydantic models and TypedDict definitions for
graph knowledge base components including entities, relationships,
and communities.
"""

from typing import Literal, TypedDict
from pydantic import BaseModel, Field, field_validator


class Entity(BaseModel):
    """Entity model for graph knowledge base.

    Represents an entity extracted from documents, such as a person,
    organization, location, product, or event.

    Attributes:
        name: Entity name (required, min length 1)
        type: Entity type (PERSON, ORG, LOCATION, PRODUCT, EVENT, CONCEPT)
        description: Brief description of the entity
        embedding: Optional vector embedding of the entity

    Example:
        >>> entity = Entity(
        ...     name="OpenAI",
        ...     type="ORG",
        ...     description="AI research company"
        ... )
    """

    name: str = Field(..., min_length=1, description="Entity name")
    type: Literal["PERSON", "ORG", "LOCATION", "PRODUCT", "EVENT", "CONCEPT"]
    description: str = Field(default="", description="Entity description")
    embedding: list[float] | None = Field(
        default=None,
        description="Vector embedding of the entity",
    )

    model_config = {
        "extra": "forbid",  # Disallow extra fields
        "validate_assignment": True,  # Validate on assignment
    }


class Relationship(BaseModel):
    """Relationship model for graph knowledge base.

    Represents a relationship between two entities, capturing the
    connection type, description, and strength.

    Attributes:
        source: Source entity name (required, min length 1)
        target: Target entity name (required, min length 1)
        type: Relationship type (e.g., WORKS_FOR, LOCATED_IN, CREATED)
        description: Brief description of the relationship
        strength: Relationship strength (0.0 to 1.0, default 1.0)

    Example:
        >>> rel = Relationship(
        ...     source="Alice",
        ...     target="OpenAI",
        ...     type="WORKS_FOR",
        ...     description="Alice works at OpenAI as a researcher",
        ...     strength=1.0
        ... )
    """

    source: str = Field(..., min_length=1, description="Source entity name")
    target: str = Field(..., min_length=1, description="Target entity name")
    type: str = Field(..., min_length=1, description="Relationship type")
    description: str = Field(
        default="",
        description="Relationship description",
    )
    strength: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Relationship strength (0.0-1.0)",
    )

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
    }

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate and normalize relationship type."""
        # Convert to uppercase and replace spaces with underscores
        return v.upper().replace(" ", "_")


class Community(BaseModel):
    """Community model for graph knowledge base.

    Represents a community of related entities detected by graph
    algorithms (e.g., Leiden, Louvain).

    Attributes:
        id: Community ID (required)
        level: Hierarchical level (>= 0)
        title: Community title (required)
        summary: Community summary (required)
        rating: Importance rating (0.0 to 1.0, default 0.0)
        entity_count: Number of entities in the community (>= 0)
        entity_ids: List of entity IDs in the community
        embedding: Optional vector embedding of the community summary

    Example:
        >>> community = Community(
        ...     id="comm_1",
        ...     level=0,
        ...     title="AI Research Community",
        ...     summary="A group of AI researchers and companies",
        ...     rating=0.9,
        ...     entity_count=5,
        ...     entity_ids=["OpenAI", "Alice", "Bob"]
        ... )
    """

    id: str = Field(..., min_length=1, description="Community ID")
    level: int = Field(..., ge=0, description="Hierarchical level")
    title: str = Field(..., min_length=1, description="Community title")
    summary: str = Field(..., min_length=1, description="Community summary")
    rating: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Importance rating (0.0-1.0)",
    )
    entity_count: int = Field(
        default=0,
        ge=0,
        description="Number of entities",
    )
    entity_ids: list[str] = Field(
        default_factory=list,
        description="List of entity IDs",
    )
    embedding: list[float] | None = Field(
        default=None,
        description="Vector embedding of the community summary",
    )

    model_config = {
        "extra": "forbid",
        "validate_assignment": True,
    }


# === TypedDict Definitions ===


class EntityDict(TypedDict, total=False):
    """TypedDict for entity data (for type hints)."""

    name: str
    type: str
    description: str
    embedding: list[float] | None


class RelationshipDict(TypedDict, total=False):
    """TypedDict for relationship data (for type hints)."""

    source: str
    target: str
    type: str
    description: str
    strength: float


class CommunityDict(TypedDict, total=False):
    """TypedDict for community data (for type hints)."""

    id: str
    level: int
    title: str
    summary: str
    rating: float
    entity_count: int
    entity_ids: list[str]
    embedding: list[float] | None


# === Literal Types ===

# Search mode for knowledge retrieval:
# - vector: Pure vector similarity search
# - graph: Graph traversal-based search
# - hybrid: Combined vector + graph search (recommended)
# - global: Community-level search for global understanding
SearchMode = Literal["vector", "graph", "hybrid", "global"]

# Community detection algorithm:
# - leiden: Higher quality, slightly slower (recommended)
# - louvain: Faster, slightly lower quality
CommunityAlgorithm = Literal["leiden", "louvain"]
