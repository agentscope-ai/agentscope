# -*- coding: utf-8 -*-
"""The knowledge document record.

A :class:`KnowledgeDocumentRecord` is the canonical source of truth
for one uploaded file inside a knowledge base.  It owns the document's
**lifecycle** (status, error, lease) and **byte handle** (``blob_uri``)
before any chunks reach the vector store, which is exactly the state
the vector store cannot represent on its own (no chunks means no
``document_id`` to aggregate from).

Top-level fields are the relational keys (``user_id`` /
``knowledge_base_id`` / ``processing_node``) **plus the lifecycle
fields the storage backend must index on** (``status`` /
``lease_expires_at``) — grouped at the top so a SQL backend can put
them straight into columns and the sweeper can query them without
deserialising :attr:`data`.  Everything else lives inside ``data`` per
the project record convention.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ._base import _RecordBase


KnowledgeDocumentStatus = Literal[
    "pending",
    "parsing",
    "chunking",
    "indexing",
    "ready",
    "error",
]
# The six lifecycle states of a knowledge document.
#
# ``pending`` — bytes are in the blob store, waiting for a worker to
# pick the document up. ``parsing`` / ``chunking`` / ``indexing`` are
# worker-owned transitions; ``ready`` and ``error`` are terminal.


class KnowledgeDocumentData(BaseModel):
    """The mutable payload of a knowledge document record.

    Groups the non-relational, non-indexed fields of the document.
    The lifecycle keys (``status`` / ``lease_expires_at``) live at
    the :class:`KnowledgeDocumentRecord` top level so the sweeper can
    query them via indexed columns without touching this payload.
    """

    filename: str = Field(
        description="Original filename supplied by the uploader.",
    )
    """The original filename — used both for citation and as the
    ``source`` field on every chunk produced from this document."""

    size: int = Field(
        ge=0,
        description="Document size in bytes as observed at upload time.",
    )
    """Byte length recorded at upload time.  Used by quota checks and
    the UI; not authoritative for parsing (the worker reopens the
    blob)."""

    content_type: str | None = Field(
        default=None,
        description="IANA media type used to route the upload to a parser.",
    )
    """IANA media type.  ``None`` lets the worker fall back to
    ``mimetypes.guess_type(filename)``."""

    blob_uri: str = Field(
        description=(
            "URI returned by the blob store after the upload was "
            "streamed in (e.g. ``local://kb/.../uuid``)."
        ),
    )
    """Scheme-qualified URI handed back by
    :class:`~agentscope.app.rag.blob_store.BlobStoreBase`.  The worker
    streams bytes back through the same blob store."""

    error: str | None = Field(
        default=None,
        description=(
            "Human-readable failure reason when ``status == 'error'``. "
            "MUST NOT include stack traces or filesystem paths — it is "
            "rendered verbatim in the UI."
        ),
    )
    """Human-readable failure reason.  Surfaced directly to the user;
    keep it short and free of sensitive content."""

    chunk_count: int = Field(
        default=0,
        ge=0,
        description="Number of chunks successfully indexed.",
    )
    """The final chunk count, written by the worker when the document
    reaches ``ready``."""


class KnowledgeDocumentRecord(_RecordBase):
    """A persisted knowledge document record.

    Top-level fields are relational keys AND the lifecycle keys the
    storage backend must index on directly (per-user listing, per-KB
    listing, per-node lease sweeps, status-based sweeps); the
    remaining mutable payload lives in :attr:`data`.
    """

    user_id: str = Field(description="The owner user id.")
    """The user id that owns the parent knowledge base."""

    knowledge_base_id: str = Field(
        description="The id of the knowledge base this document belongs to.",
    )
    """The knowledge base the document is being indexed into."""

    processing_node: str | None = Field(
        default=None,
        description=(
            "Identifier of the worker process that currently holds the "
            "lease on this document.  ``None`` if no worker is "
            "processing it."
        ),
    )
    """The current lease holder.  Promoted to the top level so the
    storage backend can look up "documents owned by node X" or
    "documents with no owner" without deserialising every payload."""

    status: KnowledgeDocumentStatus = Field(
        default="pending",
        description="Current lifecycle state.",
    )
    """Current lifecycle state.  Read by the polling endpoint, written
    by the worker as it transitions phases.  Promoted to the top level
    so the SQL backend can index it and the sweeper can filter on
    non-terminal documents without payload deserialisation."""

    lease_expires_at: datetime | None = Field(
        default=None,
        description=(
            "Wall-clock deadline for the worker that currently holds "
            "this document.  ``None`` if no worker is processing it."
        ),
    )
    """Lease deadline.  Together with ``processing_node`` lets the
    sweeper detect crashed workers and reassign their documents.
    Promoted to the top level so it can be indexed alongside
    ``status`` / ``processing_node``."""

    data: KnowledgeDocumentData
    """The mutable document payload (filename, size, blob_uri, error,
    chunk_count)."""

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_lifecycle_fields(cls, obj: Any) -> Any:
        """Lift legacy in-``data`` lifecycle fields to the top level.

        Pre-refactor payloads persisted ``status`` and
        ``lease_expires_at`` inside :class:`KnowledgeDocumentData`.
        The refactor promoted them to the record top level so the
        storage backend can index / query them without deserialising
        the payload; this validator makes the record accept both
        shapes on read so records written before the refactor still
        round-trip.

        Runs in ``mode='before'`` so it hooks every entry point that
        goes through the pydantic core validator —
        :meth:`~pydantic.BaseModel.model_validate`,
        :meth:`~pydantic.BaseModel.model_validate_json` (the path the
        storage backend takes), and direct ``__init__`` construction
        alike — giving us one place to keep the legacy compatibility
        logic.

        Args:
            obj (`Any`):
                The raw input passed to the validator.  Only ``dict``
                inputs are inspected; anything else is returned
                verbatim so subclasses / attribute-based construction
                still work.

        Returns:
            `Any`:
                The input with legacy lifecycle fields lifted from
                ``data`` to the top level when necessary.
        """
        if not isinstance(obj, dict):
            return obj
        data = obj.get("data")
        if not isinstance(data, dict):
            return obj
        # Only lift when the top level does NOT already carry the
        # field — a caller passing both wins at the top level.
        lifted: dict = {}
        for key in ("status", "lease_expires_at"):
            if key in data and key not in obj:
                lifted[key] = data.pop(key)
        if lifted:
            return {**obj, **lifted, "data": data}
        return obj
