# -*- coding: utf-8 -*-
"""The knowledge base record."""
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ._base import _RecordBase
from ._session import EmbeddingModelConfig


class KnowledgeBaseData(BaseModel):
    """The mutable payload of a knowledge base record.

    Groups every non-relational field of :class:`KnowledgeBaseRecord`
    into a single nested model so the outer record only carries
    relational keys (``user_id``) alongside the ``_RecordBase``
    envelope. This matches the convention used by the other records
    (``AgentRecord.data``, ``SessionRecord.config`` / ``.state``,
    ``TeamRecord.data`` etc.) and lets the SQL backend serialise the
    whole payload into a single JSON column without special-casing.
    """

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

    data: KnowledgeBaseData
    """The knowledge base payload (name, description, embedding config,
    vector-store collection name)."""

    @model_validator(mode="before")
    @classmethod
    def _nest_legacy_flat_payload(cls, obj: Any) -> Any:
        """Nest legacy flat-payload fields into :attr:`data`.

        Pre-refactor records stored the payload fields (``name`` /
        ``description`` / ``embedding_model_config`` /
        ``collection_name``) at the record top level rather than
        under ``data``.  This validator runs in ``mode='before'`` so
        it hooks every pydantic entry point —
        :meth:`~pydantic.BaseModel.model_validate`,
        :meth:`~pydantic.BaseModel.model_validate_json` (the path
        the storage backend takes when reading from Redis / SQL),
        and direct ``__init__`` construction — giving us one place
        to migrate the legacy shape on read.

        Args:
            obj (`Any`):
                The raw input to the validator.  Only ``dict`` inputs
                are inspected; other inputs are returned verbatim so
                attribute-based construction still works.

        Returns:
            `Any`:
                The input with legacy flat fields packed into
                ``data`` when necessary.
        """
        if not isinstance(obj, dict) or "data" in obj:
            return obj
        legacy_keys = (
            "name",
            "description",
            "embedding_model_config",
            "collection_name",
        )
        if not any(key in obj for key in legacy_keys):
            return obj
        remainder = {k: v for k, v in obj.items() if k not in legacy_keys}
        remainder["data"] = {k: obj[k] for k in legacy_keys if k in obj}
        return remainder
