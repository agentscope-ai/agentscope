# -*- coding: utf-8 -*-
"""The knowledge document record.

A :class:`KnowledgeDocumentRecord` is the canonical source of truth
for one uploaded file inside a knowledge base.  It owns the document's
**lifecycle** (status, error, lease) and **byte handle** (``blob_uri``)
before any chunks reach the vector store, which is exactly the state
the vector store cannot represent on its own (no chunks means no
``document_id`` to aggregate from).

Top-level fields are the relational keys (``user_id`` /
``knowledge_base_id`` / ``processing_node``) that the storage backend
indexes on; everything else lives inside ``data`` per the project
record convention.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    """The mutable payload of a knowledge document record."""

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

    status: KnowledgeDocumentStatus = Field(
        default="pending",
        description="Current lifecycle state.",
    )
    """Current lifecycle state.  Read by the polling endpoint, written
    by the worker as it transitions phases."""

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

    lease_expires_at: datetime | None = Field(
        default=None,
        description=(
            "Wall-clock deadline for the worker that currently holds "
            "this document.  ``None`` if no worker is processing it."
        ),
    )
    """Lease deadline.  Together with ``processing_node`` lets the
    sweeper detect crashed workers and reassign their documents."""


class KnowledgeDocumentRecord(_RecordBase):
    """A persisted knowledge document record.

    Top-level fields are relational keys the storage backend needs to
    index on directly (per-user listing, per-KB listing, per-node lease
    sweeps); the mutable payload lives in :attr:`data`.
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

    data: KnowledgeDocumentData
    """The mutable document payload."""
