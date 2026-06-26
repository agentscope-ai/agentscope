# -*- coding: utf-8 -*-
"""The knowledge base record."""
from pydantic import Field

from ._base import _RecordBase
from ._session import EmbeddingModelConfig


class KnowledgeBaseRecord(_RecordBase):
    """A persisted knowledge base record.

    Stores per-user metadata for a knowledge base; the actual chunks
    and vectors live in the configured ``VectorStoreBase`` backend.
    Each record is the canonical authorisation gate: HTTP handlers and
    middleware look the record up by ``(user_id, id)`` before talking
    to the vector store.
    """

    user_id: str = Field(description="The owner user id.")
    """The user id that owns this knowledge base."""

    name: str = Field(description="Display name of the knowledge base.")
    """Display name shown in the UI."""

    description: str = Field(
        default="",
        description="Free-form description of the knowledge base purpose.",
    )
    """Free-form description shown in the UI."""

    embedding_model_config: EmbeddingModelConfig = Field(
        description=(
            "Embedding model configuration pinned at creation time. "
            "Cannot change for the lifetime of the record because the "
            "underlying collection is sized to its dimension."
        ),
    )
    """Embedding model configuration pinned at creation time."""

    collection_name: str = Field(
        description=(
            "The vector store collection that physically backs this "
            "knowledge base. Generated server-side; opaque to clients."
        ),
    )
    """The vector store collection name (e.g. ``kb_<uuid_hex>``)."""
