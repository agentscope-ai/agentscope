# -*- coding: utf-8 -*-
"""Request / response schemas for the knowledge base router."""
from datetime import datetime

from pydantic import BaseModel, Field

from ...storage import (
    EmbeddingModelConfig,
    KnowledgeDocumentRecord,
    KnowledgeDocumentStatus,
)
from ..._service import CredentialView, KnowledgeBaseView
from ....embedding import EmbeddingModelCard
from ....rag import VectorSearchResult
from ...rag.knowledge_base_manager._dimension_policy import DimensionPolicy


class CreateKnowledgeBaseRequest(BaseModel):
    """Request body for creating a new knowledge base."""

    name: str = Field(description="Display name of the knowledge base.")
    description: str = Field(
        default="",
        description="Free-form description shown in the UI.",
    )
    embedding_model_config: EmbeddingModelConfig = Field(
        description=(
            "Embedding model used both at indexing and at query time. "
            "Cannot be changed after creation — switching would "
            "invalidate every previously inserted vector."
        ),
    )


class CreateKnowledgeBaseResponse(BaseModel):
    """Response body after creating a knowledge base."""

    knowledge_base_id: str = Field(
        description="Server-assigned knowledge base identifier.",
    )


class UpdateKnowledgeBaseRequest(BaseModel):
    """Request body for updating a knowledge base.

    Only mutable fields can be set here.  The embedding model
    configuration is pinned at creation time and cannot be changed —
    switching it would invalidate every previously inserted vector.
    """

    name: str | None = Field(
        default=None,
        description="New display name; omit to leave unchanged.",
    )
    description: str | None = Field(
        default=None,
        description="New free-form description; omit to leave unchanged.",
    )


class ListKnowledgeBasesResponse(BaseModel):
    """Response body for listing the caller's knowledge bases."""

    knowledge_bases: list[KnowledgeBaseView] = Field(
        description="All knowledge bases owned by the caller.",
    )
    total: int = Field(description="Total number of returned items.")


class KnowledgeDocumentView(BaseModel):
    """A document record as exposed to API clients.

    Surfaces both the static fields the UI needs to render a row
    (``filename`` / ``size``) and the live lifecycle fields the front
    end polls (``status`` / ``error`` / ``chunk_count``).  Internal
    fields (``user_id`` / ``blob_uri`` / ``processing_node`` / lease)
    are deliberately omitted — clients have no business introspecting
    them.
    """

    id: str = Field(description="The document identifier.")
    filename: str = Field(description="Original filename at upload time.")
    size: int = Field(description="Document size in bytes.")
    content_type: str | None = Field(
        default=None,
        description="IANA media type recorded at upload time, if any.",
    )
    status: KnowledgeDocumentStatus = Field(
        description="Current lifecycle state of the document.",
    )
    error: str | None = Field(
        default=None,
        description=(
            "Human-readable failure reason when ``status == 'error'``."
        ),
    )
    chunk_count: int = Field(
        default=0,
        description="Number of chunks indexed so far.",
    )
    created_at: datetime = Field(description="Upload timestamp.")
    updated_at: datetime = Field(
        description="Last status transition timestamp.",
    )

    @classmethod
    def from_record(
        cls,
        record: KnowledgeDocumentRecord,
    ) -> "KnowledgeDocumentView":
        """Project a storage record onto the API view.

        Centralised so router code stays a one-liner and the field
        mapping has exactly one source of truth.
        """
        return cls(
            id=record.id,
            filename=record.data.filename,
            size=record.data.size,
            content_type=record.data.content_type,
            status=record.status,
            error=record.data.error,
            chunk_count=record.data.chunk_count,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ListKnowledgeDocumentsResponse(BaseModel):
    """Response body for listing documents inside a knowledge base."""

    documents: list[KnowledgeDocumentView] = Field(
        description="One view per registered document.",
    )
    total: int = Field(description="Total number of returned items.")


class ListKnowledgeDocumentStatusResponse(BaseModel):
    """Response body for batch document-status polling."""

    items: list[KnowledgeDocumentView] = Field(
        description=(
            "Subset of the requested documents that still exist. "
            "Missing ids are silently omitted — clients may legitimately "
            "ask about a document that was deleted between two polls."
        ),
    )


class UploadKnowledgeDocumentResponse(BaseModel):
    """Response body after uploading a document into a knowledge base."""

    document_id: str = Field(
        description="Server-assigned document identifier.",
    )
    filename: str = Field(
        description="The original filename of the uploaded document.",
    )
    status: KnowledgeDocumentStatus = Field(
        description=(
            "Lifecycle state immediately after upload — always "
            "``'pending'`` in the happy path; surfaced so the client "
            "can seed its progress tracker without an extra round-trip."
        ),
    )


class SearchKnowledgeBaseRequest(BaseModel):
    """Request body for searching a knowledge base."""

    query: str = Field(description="The natural-language search query.")
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum number of results to return.",
    )


class SearchKnowledgeBaseResponse(BaseModel):
    """Response body for a knowledge base search."""

    results: list[VectorSearchResult] = Field(
        description="Matched chunks ordered by descending similarity score.",
    )
    total: int = Field(description="Total number of returned results.")


class KbEmbeddingProvider(BaseModel):
    """One credential and the embedding models it can serve.

    The model cards have been projected through the manager's
    dimension policy: incompatible models are removed and matryoshka
    cards are narrowed to the locked dimension when applicable.
    """

    credential: CredentialView = Field(
        description="The credential record exposing these models.",
    )
    models: list[EmbeddingModelCard] = Field(
        description=(
            "Embedding model cards available under this credential, "
            "filtered to those compatible with the manager's "
            "dimension policy."
        ),
    )


class ListKbEmbeddingModelsResponse(BaseModel):
    """Response body listing KB-compatible embedding models.

    The list is pre-filtered server-side against the manager's
    dimension policy.  The policy itself is also returned so the
    front-end can render an explanatory banner and lock the dimension
    selector when applicable.
    """

    providers: list[KbEmbeddingProvider] = Field(
        description=(
            "One entry per credential that has at least one "
            "compatible embedding model."
        ),
    )
    policy: DimensionPolicy = Field(
        description=(
            "The dimension policy used to filter the cards; surfaced "
            "verbatim so the UI can explain *why* models were filtered."
        ),
    )


class KbMiddlewareParametersSchemaResponse(BaseModel):
    """Response body exposing the KB middleware's parameters schema.

    The schema is derived from
    :class:`agentscope.middleware.RAGMiddleware.Parameters`
    via ``model_json_schema()`` so the front-end can render the
    session-level KB attachment form with the same schema-driven
    component used for model parameters.
    """

    parameter_schema: dict = Field(
        description=(
            "JSON Schema produced by `RAGMiddleware.Parameters"
            "model_json_schema()`.  Shaped identically to the "
            "`parameter_schema` field on `ModelCard`."
        ),
    )


class ListSupportedContentTypesResponse(BaseModel):
    """Response body advertising the parser-supported upload types.

    Aggregated across every parser registered on the app — the union of
    each parser's :attr:`supported_media_types` and
    :meth:`supported_extensions`.  The front-end uses this to populate
    ``<input accept>`` and to reject unsupported drops on the client
    before the file leaves the browser.
    """

    media_types: list[str] = Field(
        description=(
            "Union of IANA media types every registered parser claims "
            "to handle.  Deduplicated and sorted."
        ),
    )
    extensions: list[str] = Field(
        description=(
            "Filename extensions (each starting with `.`) every "
            "registered parser claims to handle.  Deduplicated and "
            "sorted.  Derived from `mimetypes` by the base parser; "
            "subclasses may override the default."
        ),
    )
