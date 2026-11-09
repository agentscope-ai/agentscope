# -*- coding: utf-8 -*-
"""Knowledge base router — manage knowledge bases and their documents.

A knowledge base is the user-facing concept; physically each one maps
to a single vector store collection (in the MVP isolation strategy).
The HTTP layer is intentionally thin — every endpoint translates the
request into a single :class:`~agentscope.app._service.
KnowledgeBaseService` call and returns the result.
"""
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from ..access import ResourceKind
from ..deps import (
    get_current_user_id,
    get_download_secret,
    get_knowledge_base_manager,
    get_knowledge_base_service,
    get_knowledge_chunkers,
    get_knowledge_parsers,
    get_resource_access_service,
)
from ._schema import (
    ChunkerInfo,
    DocumentDownloadTokenResponse,
    ListDocumentChunksResponse,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeBaseResponse,
    KbEmbeddingProvider,
    KbMiddlewareParametersSchemaResponse,
    KnowledgeDocumentView,
    ListChunkersResponse,
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
    sign_download_token,
    verify_download_token,
    KnowledgeBaseView,
    ResourceAccessService,
)
from ...middleware import RAGMiddleware
from ...rag import ChunkerBase, ParserBase


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
    "/chunkers",
    response_model=ListChunkersResponse,
    summary="List available chunker types and their parameter schemas",
)
async def list_chunkers(
    _: str = Depends(get_current_user_id),
    chunker_classes: list[type[ChunkerBase]] = Depends(
        get_knowledge_chunkers,
    ),
) -> ListChunkersResponse:
    """List every available chunker type with its parameter schema.

    The front-end uses this to populate the chunker selector and
    to render a dynamic parameter form when creating a knowledge
    base.

    Args:
        _ (`str`):
            Injected authenticated user ID; only used to gate the
            endpoint behind authentication.
        chunker_classes (`list[type[ChunkerBase]]`):
            The chunker classes configured via
            ``create_app(knowledge_chunkers=...)``.

    Returns:
        `ListChunkersResponse`:
            All available chunker types with their JSON Schemas.
    """
    return ListChunkersResponse(
        chunkers=[
            ChunkerInfo(
                type=cls.chunker_type,
                parameter_schema=cls.Parameters.model_json_schema(),
            )
            for cls in chunker_classes
        ],
    )


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
        chunker_config=body.chunker_config,
    )
    return CreateKnowledgeBaseResponse(knowledge_base_id=record.id)


@knowledge_base_router.get(
    "/",
    response_model=ListKnowledgeBasesResponse,
    summary="List the caller's knowledge bases",
)
async def list_knowledge_bases(
    id: str  # pylint: disable=redefined-builtin  # noqa: A002
    | None = Query(
        default=None,
        description=(
            "Filter down to one knowledge base — the list endpoint "
            "doubles as get-single, RAGFlow style."
        ),
    ),
    name: str
    | None = Query(
        default=None,
        description="Case-insensitive substring filter on the name.",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    page_size: int = Query(default=30, ge=1, le=128),
    orderby: str = Query(
        default="create_time",
        pattern="^(create_time|update_time)$",
    ),
    desc: bool = Query(default=True, description="Sort newest first."),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> ListKnowledgeBasesResponse:
    """Return the caller's knowledge bases, filtered and paginated.

    Includes the caller's own knowledge bases plus any shared to them
    through :class:`ResourceAccessPolicyBase`. Each entry carries an
    ``editable`` flag (``read`` grants search + attach; ``edit`` also
    grants document add/delete and metadata update) plus the
    aggregated document / chunk / per-status counts and the resolved
    ``credential_name`` a detail page needs — the list is the single
    source of truth; there is no separate get-single endpoint.

    Args:
        id (`str | None`, optional):
            Filter down to one knowledge base by id.
        name (`str | None`, optional):
            Case-insensitive substring filter on the display name.
        page (`int`, defaults to ``1``):
            1-based page number.
        page_size (`int`, defaults to ``30``):
            Knowledge bases per page (max 128).
        orderby (`str`, defaults to ``"create_time"``):
            Sort key — ``"create_time"`` or ``"update_time"``.
        desc (`bool`, defaults to ``True``):
            Sort newest first.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `ListKnowledgeBasesResponse`:
            The requested page of views plus the filtered total.
    """
    views, total = await service.list_knowledge_base_views(
        user_id,
        knowledge_base_id=id,
        name=name,
        page=page,
        page_size=page_size,
        orderby=orderby,
        desc=desc,
    )
    return ListKnowledgeBasesResponse(
        knowledge_bases=views,
        total=total,
        page=page,
        page_size=page_size,
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
    id: str  # pylint: disable=redefined-builtin  # noqa: A002
    | None = Query(
        default=None,
        description="Filter down to one document by id.",
    ),
    keywords: str
    | None = Query(
        default=None,
        description="Case-insensitive substring filter on the filename.",
    ),
    status_filter: str
    | None = Query(
        default=None,
        alias="status",
        pattern="^(pending|parsing|chunking|indexing|ready|error)$",
        description="Filter by indexing status.",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    page_size: int = Query(default=30, ge=1, le=128),
    orderby: str = Query(
        default="create_time",
        pattern="^(create_time|update_time)$",
    ),
    desc: bool = Query(default=True, description="Sort newest first."),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> ListKnowledgeDocumentsResponse:
    """List a knowledge base's documents, filtered and paginated.

    Reads from the storage backend (service-mode source of truth), so
    documents in any lifecycle state — including ``pending`` /
    ``parsing`` / ``error`` — are returned alongside ``ready`` ones.

    Args:
        knowledge_base_id (`str`):
            The target knowledge base id.
        id (`str | None`, optional):
            Filter down to one document by id.
        keywords (`str | None`, optional):
            Case-insensitive substring filter on the filename.
        status_filter (`str | None`, optional):
            Filter by indexing status.
        page (`int`, defaults to ``1``):
            1-based page number.
        page_size (`int`, defaults to ``30``):
            Documents per page (max 128).
        orderby (`str`, defaults to ``"create_time"``):
            Sort key — ``"create_time"`` or ``"update_time"``.
        desc (`bool`, defaults to ``True``):
            Sort newest first.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `ListKnowledgeDocumentsResponse`:
            The requested page of views plus the filtered total.
    """
    records, total = await service.list_documents(
        user_id,
        knowledge_base_id,
        document_id=id,
        keywords=keywords,
        doc_status=status_filter,
        page=page,
        page_size=page_size,
        orderby=orderby,
        desc=desc,
    )
    views = [KnowledgeDocumentView.from_record(r) for r in records]
    return ListKnowledgeDocumentsResponse(
        documents=views,
        total=total,
        page=page,
        page_size=page_size,
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
        status=record.status,
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


# The media types a browser may render inline. Anything else (notably
# text/html and image/svg+xml, which can run script) is forced to an
# attachment so a crafted upload cannot XSS the app origin. Raster
# image formats are enumerated rather than matched by ``image/``
# prefix precisely so that SVG never slips through.
_INLINE_MEDIA_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
    },
)


# Preview tokens outlive a download capability on purpose: a PDF
# viewer embedded in an <iframe> re-requests the file as the reader
# pages through it, and a 60-second window would 401 mid-read.
_PREVIEW_TOKEN_TTL = 600


def _document_token_path(knowledge_base_id: str, document_id: str) -> str:
    """The resource string a document download token is bound to."""
    return f"kb/{knowledge_base_id}/{document_id}"


@knowledge_base_router.get(
    "/{knowledge_base_id}/documents/{document_id}/chunks",
    response_model=ListDocumentChunksResponse,
    summary="Browse one document's chunks in order",
)
async def list_document_chunks(
    knowledge_base_id: str = Path(description="The knowledge base id."),
    document_id: str = Path(description="The document id."),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    page_size: int = Query(default=30, ge=1, le=128),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
) -> ListDocumentChunksResponse:
    """Return one page of a document's chunks, ``chunk_index`` ascending.

    Pagination is stable: ``chunk_index`` is a dense, immutable
    ``0..N-1`` sequence within a document, so page ``N`` always maps to
    the same chunk range.  A document that is still indexing serves
    the chunks persisted so far.

    Args:
        knowledge_base_id (`str`):
            The parent knowledge base.
        document_id (`str`):
            The document whose chunks should be listed.
        page (`int`, defaults to ``1``):
            1-based page number.
        page_size (`int`, defaults to ``30``):
            Chunks per page (max 128).
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.

    Returns:
        `ListDocumentChunksResponse`:
            The page of chunks plus the document's total chunk count.
    """
    chunks, total = await service.list_document_chunks(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        page=page,
        page_size=page_size,
    )
    return ListDocumentChunksResponse(
        chunks=chunks,
        total=total,
        page=page,
        page_size=page_size,
    )


@knowledge_base_router.post(
    "/{knowledge_base_id}/documents/{document_id}/download_token",
    response_model=DocumentDownloadTokenResponse,
    summary="Mint a short-lived token for a browser-native fetch",
)
async def create_document_download_token(
    knowledge_base_id: str = Path(description="The knowledge base id."),
    document_id: str = Path(description="The document id."),
    user_id: str = Depends(get_current_user_id),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
    download_secret: str = Depends(get_download_secret),
) -> DocumentDownloadTokenResponse:
    """Mint a token so a browser can fetch the raw file directly.

    ``<iframe>`` PDF previews, ``<img>`` tags and click-to-download
    navigations carry no custom headers, so they cannot present
    ``X-User-ID``; the token rides in the URL instead and is bound to
    exactly this document.

    Args:
        knowledge_base_id (`str`):
            The parent knowledge base.
        document_id (`str`):
            The document the token authorizes.
        user_id (`str`):
            Injected authenticated user ID.
        service (`KnowledgeBaseService`):
            Injected knowledge base service — resolved here only to
            fail early with a proper 404 instead of a raw error page
            on the browser navigation.
        download_secret (`str`):
            Injected app-wide signing secret.

    Returns:
        `DocumentDownloadTokenResponse`:
            The token and its expiry.
    """
    await service.get_document(user_id, knowledge_base_id, document_id)
    token, expires_at = sign_download_token(
        download_secret,
        user_id,
        _document_token_path(knowledge_base_id, document_id),
        ttl=_PREVIEW_TOKEN_TTL,
    )
    return DocumentDownloadTokenResponse(token=token, expires_at=expires_at)


@knowledge_base_router.get(
    "/{knowledge_base_id}/documents/{document_id}",
    summary="Fetch the original uploaded file of a document",
)
async def read_knowledge_document(
    knowledge_base_id: str = Path(description="The knowledge base id."),
    document_id: str = Path(description="The document id."),
    download: bool = Query(
        default=False,
        description="Force a Content-Disposition attachment.",
    ),
    token: str
    | None = Query(
        default=None,
        description=(
            "A token from ``POST .../documents/{document_id}"
            "/download_token``, accepted in place of the "
            "``X-User-ID`` header so a browser navigation can fetch "
            "the file directly."
        ),
    ),
    x_user_id: str | None = Header(default=None),
    service: "KnowledgeBaseService" = Depends(get_knowledge_base_service),
    download_secret: str = Depends(get_download_secret),
) -> StreamingResponse:
    """Stream the raw uploaded file back for preview or download.

    Mirrors ``GET /workspace/files``: the body is piped chunk by chunk
    from the blob store rather than read whole, so one large file
    cannot exhaust the shared API process.  Only media types that
    cannot carry script are served inline; everything else is forced
    to an attachment.

    Args:
        knowledge_base_id (`str`):
            The parent knowledge base.
        document_id (`str`):
            The document whose original file should be fetched.
        download (`bool`, defaults to ``False``):
            Force an attachment disposition.
        token (`str | None`, optional):
            Signed download token, accepted instead of ``X-User-ID``.
        x_user_id (`str | None`, optional):
            The normal identity header.
        service (`KnowledgeBaseService`):
            Injected knowledge base service.
        download_secret (`str`):
            Injected app-wide signing secret.

    Returns:
        `StreamingResponse`:
            The file bytes with content-type / length / disposition
            headers derived from the upload-time record.
    """
    if token is not None:
        try:
            user_id = verify_download_token(
                download_secret,
                token,
                _document_token_path(knowledge_base_id, document_id),
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            ) from e
    elif x_user_id:
        user_id = x_user_id
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-ID header or download token is required.",
        )

    record, size, content = await service.stream_document_content(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )
    data = record.data
    media_type = (data.content_type or "").split(";")[
        0
    ].strip().lower() or "application/octet-stream"
    inline = not download and media_type in _INLINE_MEDIA_TYPES
    disposition = "inline" if inline else "attachment"
    filename = quote(data.filename or "download")
    headers = {
        "Content-Disposition": (f"{disposition}; filename*=UTF-8''{filename}"),
        "Cache-Control": "private, max-age=60",
        # The declared type is authoritative — never let the browser
        # sniff a scriptable type out of the bytes.
        "X-Content-Type-Options": "nosniff",
    }
    # Measured on the blob, never read off the record: a wrong length
    # truncates the body. Present, it turns the browser's download
    # progress from a byte counter into a percentage; absent (backend
    # cannot measure), the response is chunked — same rule as
    # ``GET /workspace/files``.
    if size is not None:
        headers["Content-Length"] = str(size)
    return StreamingResponse(
        content,
        media_type=media_type,
        headers=headers,
    )
