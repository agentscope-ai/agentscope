# -*- coding: utf-8 -*-
"""The knowledge base record."""
from pydantic import BaseModel, Field

from ._base import _RecordBase
from ._session import EmbeddingModelConfig


class ChunkerConfig(BaseModel):
    """Chunker configuration persisted alongside a knowledge base.

    Stores the chunker ``type`` identifier (registered via
    :func:`~agentscope.rag.get_chunker_registry`) and the
    keyword-argument ``parameters`` used to reconstruct it at
    indexing time.  Defaults to ``approx_token`` with empty
    parameters (uses the chunker's own defaults).
    """

    type: str = Field(
        default="approx_token",
        description=(
            "Chunker type identifier, as declared by "
            "``ChunkerBase.chunker_type``."
        ),
    )
    parameters: dict = Field(
        default_factory=dict,
        description=(
            "Keyword arguments forwarded to " "``ChunkerBase.from_config()``."
        ),
    )


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

    chunker_config: ChunkerConfig | None = Field(
        default=None,
        description=(
            "Chunker configuration pinned at creation time. "
            "``None`` only for legacy records created before "
            "per-KB chunker support was introduced; the index "
            "worker falls back to ``ApproxTokenChunker()`` in "
            "that case.  New records always have an explicit value."
        ),
    )
    """Chunker configuration pinned at creation time.

    ``None`` only for legacy records; new creations always set this.
    """

    collection_name: str = Field(
        description=(
            "The vector store collection that physically backs this "
            "knowledge base. Generated server-side; opaque to clients."
        ),
    )
    """The vector store collection name (e.g. ``kb_<uuid_hex>``)."""
