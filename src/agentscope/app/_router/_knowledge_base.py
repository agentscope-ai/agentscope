# -*- coding: utf-8 -*-
"""Knowledge base router — manage knowledge bases and their documents.

A knowledge base is the user-facing concept; physically each one maps
to a single vector store collection (in the MVP isolation strategy).
The HTTP layer is intentionally thin — every endpoint translates the
request into a single :class:`~agentscope.app._service.
KnowledgeBaseService` call and returns the result.
"""
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Path,
    Query,
    UploadFile,
    status,
)

from ..access import ResourceKind
from ..deps import (
    get_current_user_id,
    get_knowledge_base_manager,
    get_knowledge_base_service,
    get_knowledge_parsers,
    get_resource_access_service,
)
from ._schema import (
    CreateKnowledgeBaseRequest,
    CreateKnowledgeBaseResponse,
    KbEmbeddingProvider,
    KbMiddlewareParametersSchemaResponse,
    KnowledgeDocumentView,
    ListKbEmbeddingModelsResponse,
    ListKnowledgeBasesResponse,
    ListKnowledgeDocumentsResponse,
    ListKnowledgeDocumentStatusResponse,
    ListSupportedContentTypesResponse,
    SearchKnowledgeBaseRequest,
    SearchKnowledgeBaseResponse,
    UpdateKnowledgeBaseRequest,
    UploadKnowledgeDocumentResponse,
)
from ...credential import CredentialFactory
from ..rag.knowledge_base_manager import KnowledgeBaseManagerBase
from .._service import (
    KnowledgeBaseService,
    KnowledgeBaseView,
    ResourceAccessService,
)
from ...middleware import RAGMiddleware
from ...rag import ParserBase


knowledge_base_router = APIRouter(
    prefix="/knowledge_bases",
    tags=["knowledge_bases"],
    responses={404: {"description": "Not found"}},
)


@knowledge_base_router.get(
    "/embedding_models",
    response_model=ListKbEmbeddingModelsResponse,
    summary="List embedding models compatible with the KB dimension policy",
)
async def list_kb_embedding_models(
    user_id: str = Depends(get_current_user_id),
    access: "ResourceAccessService" = Depends(get_resource_access_service),
    manager: "KnowledgeBaseManagerBase" = Depends(
        get_knowledge_base_manager,
    ),
) -> ListKbEmbeddingModelsResponse:
    """List embedding models the user can pick at KB-creation time.

    Walks every credential visible to the caller (own + shared via the
    resource access policy), looks up each provider's embedding model
    class, gathers its model cards, and projects each card through the
    manager's :class:`DimensionPolicy`. Incompatible cards are
    dropped; matryoshka cards under a ``FIXED`` /
    ``LOCKED_BY_EXISTING`` policy are narrowed to the locked
    dimension.  Providers that end up with zero compatible models are
    omitted from the response entirely.

    Args:
        user_id (`str`):
            Injected authenticated user ID.
        access (`ResourceAccessService`):
            Injected resource access service; enumerates visible
            credentials (own + shared) so KB creation works against
            shared credentials too.
        manager (`KnowledgeBaseManagerBase`):
            Injected knowledge base manager.

    Returns:
        `ListKbEmbeddingModelsResponse`:
            One entry per credential with at least one compatible
            embedding model, plus the policy used for filtering.
    """
    policy = await manager.get_dimension_policy()
    credentials = await access.list_resource(user_id, ResourceKind.CREDENTIAL)

    providers: list[KbEmbeddingProvider] = []
    for credential in credentials:
        credential_type = credential.data.get("type")
        if not credential_type:
            continue
        credential_cls = CredentialFactory.get_credential_class(
            credential_type,
        )
        if credential_cls is None:
            continue
        embedding_cls = credential_cls.get_embedding_model_class()
        if embedding_cls is None:
            continue

        filtered = []
        for card in embedding_cls.list_models():
            projected = policy.filter_card(card)
            if projected is not None:
                filtered.append(projected)
        if not filtered:
            continue
        providers.append(
            KbEmbeddingProvider(credential=credential, models=filtered),
        )

    return ListKbEmbeddingModelsResponse(providers=providers, policy=policy)


@knowledge_base_router.get(
    "/middleware/parameters_schema",
    response_model=KbMiddlewareParametersSchemaResponse,
    summary="JSON Schema for the KB middleware's tunable parameters",
)
async def get_kb_middleware_parameters_schema(
    _: str = Depends(get_current_user_id),
) -> KbMiddlewareParametersSchemaResponse:
    """Return the parameter schema for
    :class:`agentscope.middleware.RAGMiddleware`.

    The schema is shaped like every other ``parameter_schema`` served
    by this service — title / description / default / enum / minimum
    / maximum — so the front-end can render the session-level KB
    attachment form with the same schema-driven component used for
    model parameters.

    Args:
        _ (`str`):
            Injected authenticated user ID; only used to gate the
            endpoint behind authentication.

    Returns:
        `KbMiddlewareParametersSchemaResponse`:
            The JSON Schema describing the middleware's
            user-tunable parameters.
    """
    return KbMiddlewareParametersSchemaResponse(
        parameter_schema=(RAGMiddleware.Parameters.model_json_schema()),
    )


@knowledge_base_router.get(
    "/supported_content_types",
    response_model=ListSupportedContentTypesResponse,
    summary="List file types the configured parsers can ingest",
)
async def list_supported_content_types(
    _: str = Depends(get_current_user_id),
    parsers: list[ParserBase]
    | dict[str, ParserBase] = Depends(
        get_knowledge_parsers,
    ),
) -> ListSupportedContentTypesResponse:
    """Advertise the union of media types and filename extensions every
    registered parser accepts.

    Used by the front-end to populate the document picker's ``accept``
    attribute and to reject drag-dropped files whose extension lies
    outside the supported set before the upload starts.  Routing on
    upload still goes through the media type — this endpoint is a
    capability hint, not authoritative dispatch.

    Args:
        _ (`str`):
            Injected authenticated user ID; only used to gate the
            endpoint behind authentication.
        parsers (`list[ParserBase] | dict[str, ParserBase]`):
            Injected parser registry — the same value the index worker
            uses to dispatch uploads.

    Returns:
        `ListSupportedContentTypesResponse`:
            Deduplicated, sorted unions of ``media_types`` and
            ``extensions``.
    """
    parser_iter = parsers.values() if isinstance(parsers, dict) else parsers
    media_types: set[str] = set()
    extensions: set[str] = set()
    for parser in parser_iter:
        media_types.update(parser.supported_media_types)
        extensions.update(parser.supported_extensions())
    return ListSupportedContentTypesResponse(
        media_types=sorted(media_types),
        extensions=sorted(extensions),
    )


# ----------------------------------------------------------------------
# Knowledge base management
# ----------------------------------------------------------------------


@knowledge_base_router.post(
    "/",
    response_model=CreateKnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new knowledge base",
)
async def create_knowledge_base(
    body: CreateKnowledgeBaseRequest,
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> CreateKnowledgeBaseResponse:
    """Create a new knowledge base for the authenticated user.

    Allocates a fresh vector store collection sized to the embedding
    model's output dimension and persists the knowledge base record.

    Args:
        body (`CreateKnowledgeBaseRequest`):
            Knowledge base name, description, and embedding model
            configuration.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `CreateKnowledgeBaseResponse`:
            The server-assigned knowledge base identifier.
    """
    record = await service.create_knowledge_base(
        user_id=user_id,
        name=body.name,
        description=body.description,
        embedding_model_config=body.embedding_model_config,
    )
    return CreateKnowledgeBaseResponse(knowledge_base_id=record.id)


@knowledge_base_router.get(
    "/",
    response_model=ListKnowledgeBasesResponse,
    summary="List the caller's knowledge bases",
)
async def list_knowledge_bases(
    user_id: str = Depends(get_current_user_id),
    access: "ResourceAccessService" = Depends(get_resource_access_service),
) -> ListKnowledgeBasesResponse:
    """Return all knowledge bases visible to the authenticated user.

    Includes the caller's own knowledge bases plus any shared to them
    through :class:`ResourceAccessPolicyBase`. Each entry carries an
    ``editable`` flag: ``read`` grants search + attach; ``edit`` also
    grants document add/delete and metadata update.

    Args:
        user_id (`str`):
            Injected authenticated user ID.
        access (`ResourceAccessService`):
            Injected resource access service.

    Returns:
        `ListKnowledgeBasesResponse`:
            All visible knowledge bases paired with per-viewer
            editability.
    """
    entries = await access.list_resource(user_id, ResourceKind.KNOWLEDGE_BASE)
    return ListKnowledgeBasesResponse(
        knowledge_bases=entries,
        total=len(entries),
    )


@knowledge_base_router.patch(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseView,
    summary="Update mutable fields on a knowledge base",
)
async def update_knowledge_base(
    body: UpdateKnowledgeBaseRequest,
    knowledge_base_id: str = Path(description="The knowledge base id."),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> KnowledgeBaseView:
    """Update mutable fields on a knowledge base.

    Only ``name`` and ``description`` can be updated.  The embedding
    model configuration is pinned at creation time and cannot be
    changed.

    Args:
        body (`UpdateKnowledgeBaseRequest`):
            The fields to update; omitted fields stay unchanged.
        knowledge_base_id (`str`):
            The knowledge base to update.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `KnowledgeBaseView`:
            The knowledge base record after the update.
    """
    record = await service.update_knowledge_base(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        name=body.name,
        description=body.description,
    )
    # Only reachable via ``_require_edit`` inside the service, so the
    # caller definitionally has edit permission.
    return KnowledgeBaseView.model_validate(
        {**record.model_dump(), "editable": True},
    )


@knowledge_base_router.delete(
    "/{knowledge_base_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a knowledge base",
)
async def delete_knowledge_base(
    knowledge_base_id: str = Path(description="The knowledge base id."),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> None:
    """Permanently delete a knowledge base.

    Drops the underlying vector store collection together with every
    associated document and the knowledge base record itself.

    Args:
        knowledge_base_id (`str`):
            The knowledge base to delete.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.
    """
    await service.delete_knowledge_base(user_id, knowledge_base_id)


# ----------------------------------------------------------------------
# Document management
# ----------------------------------------------------------------------


@knowledge_base_router.get(
    "/{knowledge_base_id}/documents",
    response_model=ListKnowledgeDocumentsResponse,
    summary="List documents registered in a knowledge base",
)
async def list_knowledge_documents(
    knowledge_base_id: str = Path(description="The knowledge base id."),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> ListKnowledgeDocumentsResponse:
    """List every document registered against a knowledge base.

    Reads from the storage backend (service-mode source of truth), so
    documents in any lifecycle state — including ``pending`` /
    ``parsing`` / ``error`` — are returned alongside ``ready`` ones.

    Args:
        knowledge_base_id (`str`):
            The target knowledge base id.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `ListKnowledgeDocumentsResponse`:
            One view per registered document.
    """
    records = await service.list_documents(user_id, knowledge_base_id)
    views = [KnowledgeDocumentView.from_record(r) for r in records]
    return ListKnowledgeDocumentsResponse(
        documents=views,
        total=len(views),
    )


@knowledge_base_router.get(
    "/{knowledge_base_id}/documents/status",
    response_model=ListKnowledgeDocumentStatusResponse,
    summary="Batch-query indexing status of one or more documents",
)
async def list_knowledge_document_status(
    knowledge_base_id: str = Path(description="The knowledge base id."),
    ids: str = Query(
        description=(
            "Comma-separated list of document ids to query. "
            "Missing ids are silently omitted from the response."
        ),
    ),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> ListKnowledgeDocumentStatusResponse:
    """Return the current lifecycle state of a batch of documents.

    Designed for the front-end's status polling loop: the page sends
    every in-flight document id at once so per-document round-trips
    do not multiply with concurrency.

    Args:
        knowledge_base_id (`str`):
            The target knowledge base id.
        ids (`str`):
            Comma-separated document ids.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `ListKnowledgeDocumentStatusResponse`:
            Views for the matched documents.
    """
    document_ids = [tok for tok in (s.strip() for s in ids.split(",")) if tok]
    records = await service.get_document_status(
        user_id,
        knowledge_base_id,
        document_ids,
    )
    return ListKnowledgeDocumentStatusResponse(
        items=[KnowledgeDocumentView.from_record(r) for r in records],
    )


@knowledge_base_router.post(
    "/{knowledge_base_id}/documents",
    response_model=UploadKnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document into a knowledge base",
)
async def upload_knowledge_document(
    knowledge_base_id: str = Path(description="The knowledge base id."),
    file: UploadFile = File(
        description="The document to index (PDF, TXT, Markdown, …).",
    ),
    content_type: str
    | None = Form(
        default=None,
        description=(
            "Override the IANA media type used to route the upload. "
            "Defaults to the type guessed from the filename."
        ),
    ),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> UploadKnowledgeDocumentResponse:
    """Register an uploaded document and dispatch it for indexing.

    The HTTP connection covers only the upload phase: the request body
    is streamed into the blob store, a ``pending`` document record is
    persisted, the indexing task is dispatched, and the response is
    returned.  Parsing / chunking / embedding happen asynchronously in
    a worker; the client tracks progress via
    :func:`list_knowledge_document_status`.

    Args:
        knowledge_base_id (`str`):
            The knowledge base to receive the document.
        file (`UploadFile`):
            The uploaded file (multipart/form-data).
        content_type (`str | None`, optional):
            Override the IANA media type used to route the upload.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `UploadKnowledgeDocumentResponse`:
            The server-assigned document id, filename, and the
            initial lifecycle state (always ``"pending"``).
    """
    record = await service.register_document(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        filename=file.filename or "uploaded_file",
        stream=file.file,
        size=file.size or 0,
        content_type=content_type or file.content_type,
    )
    return UploadKnowledgeDocumentResponse(
        document_id=record.id,
        filename=record.data.filename,
        status=record.data.status,
    )


@knowledge_base_router.delete(
    "/{knowledge_base_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document from a knowledge base",
)
async def delete_knowledge_document(
    knowledge_base_id: str = Path(description="The knowledge base id."),
    document_id: str = Path(description="The document id."),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> None:
    """Remove a document and all its chunks from a knowledge base.

    Args:
        knowledge_base_id (`str`):
            The knowledge base the document belongs to.
        document_id (`str`):
            The document to delete.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.
    """
    await service.delete_document(user_id, knowledge_base_id, document_id)


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------


@knowledge_base_router.post(
    "/{knowledge_base_id}/search",
    response_model=SearchKnowledgeBaseResponse,
    summary="Search a knowledge base by natural-language query",
)
async def search_knowledge_base(
    body: SearchKnowledgeBaseRequest,
    knowledge_base_id: str = Path(description="The knowledge base id."),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> SearchKnowledgeBaseResponse:
    """Run a similarity search over a knowledge base.

    Embeds the query with the knowledge base's configured embedding
    model and returns the top-K most similar chunks.

    Args:
        body (`SearchKnowledgeBaseRequest`):
            The query text and ``top_k``.
        knowledge_base_id (`str`):
            The knowledge base to search.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `SearchKnowledgeBaseResponse`:
            Matched chunks ordered by descending similarity.
    """
    results = await service.search(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        query=body.query,
        top_k=body.top_k,
    )
    return SearchKnowledgeBaseResponse(results=results, total=len(results))
