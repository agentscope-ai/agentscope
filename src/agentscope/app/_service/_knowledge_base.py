# -*- coding: utf-8 -*-
"""Knowledge base service: HTTP-side orchestration.

The router stays thin and DTO-shaped; everything HTTP-side that needs
to coordinate persistence, the blob store, the indexing pipeline,
and the vector store goes through this service.

The split with :class:`~agentscope.rag.KnowledgeBase`
is deliberate.  ``KnowledgeBase`` is a **library-mode** handle that only
depends on the vector store; embedded users instantiate one and drive
the parse → chunk → embed pipeline themselves.  ``KnowledgeBaseService``
is **service-mode** orchestration: it owns the document records
(status / blob / lease) and is the single source of truth for "what
documents exist in this KB" when the app is running over HTTP.  The
two views are intentionally not blended — mixing library-mode inserts
with service-mode listing would leave records out of sync, and the
project's stance is that a knowledge base is managed end-to-end in one
mode.
"""
import uuid
from collections import Counter
from typing import IO, TYPE_CHECKING, AsyncIterator

from fastapi import HTTPException, status
from pydantic import ValidationError

from ..access import ResourceKind
from ..rag.knowledge_base_manager import (
    DimensionPolicyError,
    KnowledgeBaseNotFoundError,
)
from ..storage import (
    ChunkerConfig,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
)
from ..._logging import logger
from ...rag import ApproxTokenChunker, Chunk
from .._bus_ops import enqueue_index_task
from ._access import (
    KnowledgeBaseStatusCounts,
    KnowledgeBaseView,
    ResourceAccessService,
)

if TYPE_CHECKING:
    from ..rag.blob_store import BlobStoreBase
    from ..rag.knowledge_base_manager import KnowledgeBaseManagerBase
    from ..message_bus import MessageBus
    from ..storage import (
        EmbeddingModelConfig,
        KnowledgeBaseRecord,
        StorageBase,
    )
    from ...rag import ChunkerBase, VectorSearchResult


class KnowledgeBaseService:
    """HTTP service for knowledge bases.

    Owns the document lifecycle in service mode: register on upload,
    enqueue an index task, query status during indexing, and clean up
    record + blob + vector store on delete.  All parsing / chunking /
    embedding work happens inside the
    :class:`~agentscope.app._service.IndexWorker`; the service only
    hands off (via the message bus) and observes.
    """

    def __init__(
        self,
        storage: "StorageBase",
        knowledge_base_manager: "KnowledgeBaseManagerBase",
        blob_store: "BlobStoreBase",
        message_bus: "MessageBus",
        resource_access_service: "ResourceAccessService",
        chunkers: "list[type[ChunkerBase]] | None" = None,
    ) -> None:
        """Initialize the service.

        Args:
            storage (`StorageBase`):
                The application storage backend; documents are
                persisted here, not inside the vector store.
            knowledge_base_manager (`KnowledgeBaseManagerBase`):
                Resolves the :class:`KnowledgeBase` runtime used to clear
                vector store records on document deletion.
            blob_store (`BlobStoreBase`):
                Owns the bytes from upload until the worker is done.
                The service writes on upload and deletes on document
                removal.
            message_bus (`MessageBus`):
                Application message bus.  The service publishes one
                index-task entry per uploaded document via
                :func:`~agentscope.app._bus_ops.enqueue_index_task`;
                a co-located or out-of-process
                :class:`IndexTaskConsumer` drains and processes them.
            resource_access_service (`ResourceAccessService`):
                Cross-owner access resolver. Every KB lookup (read
                *and* mutation) goes through it so shared knowledge
                bases work end-to-end: readers see documents +
                search hits, editors can also upload / delete.
            chunkers (`list[type[ChunkerBase]] | None`, optional):
                The chunker classes users can choose from when creating
                a knowledge base; used to validate ``chunker_config``.
                Defaults to ``[ApproxTokenChunker]``.
        """
        self._storage = storage
        self._manager = knowledge_base_manager
        self._blob_store = blob_store
        self._bus = message_bus
        self._access = resource_access_service
        self._chunkers_by_type = {
            cls.chunker_type: cls for cls in (chunkers or [ApproxTokenChunker])
        }

    # ------------------------------------------------------------------
    # Knowledge base CRUD
    # ------------------------------------------------------------------

    async def create_knowledge_base(
        self,
        user_id: str,
        name: str,
        description: str,
        embedding_model_config: "EmbeddingModelConfig",
        chunker_config: "ChunkerConfig | None" = None,
    ) -> "KnowledgeBaseRecord":
        """Delegate creation to the manager, mapping policy errors.

        Args:
            user_id (`str`):
                The owner user id.
            name (`str`):
                Display name.
            description (`str`):
                Free-form description.
            embedding_model_config (`EmbeddingModelConfig`):
                Embedding model configuration; pinned to the record.
            chunker_config (`ChunkerConfig | None`, optional):
                Chunker configuration; pinned to the record.  Defaults
                to the first configured chunker with default parameters.

        Returns:
            `KnowledgeBaseRecord`:
                The newly persisted record.

        Raises:
            `HTTPException`:
                ``409`` when the requested embedding dimension
                violates the manager's dimension policy.
                ``422`` when the chunker type or parameters are
                invalid.
        """
        if chunker_config is None:
            chunker_config = ChunkerConfig(
                type=next(iter(self._chunkers_by_type)),
                parameters={},
            )
        chunker_cls = self._chunkers_by_type.get(chunker_config.type)
        if chunker_cls is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unknown chunker type: {chunker_config.type!r}, "
                    f"available: {sorted(self._chunkers_by_type)}"
                ),
            )
        try:
            chunker_cls.Parameters(**chunker_config.parameters)
        except (TypeError, ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid chunker parameters: {exc}",
            ) from exc

        try:
            return await self._manager.create_knowledge_base(
                user_id=user_id,
                name=name,
                description=description,
                embedding_model_config=embedding_model_config,
                chunker_config=chunker_config,
            )
        except DimensionPolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    async def list_knowledge_bases(
        self,
        user_id: str,
    ) -> "list[KnowledgeBaseRecord]":
        """List all knowledge base records owned by the given user.

        Args:
            user_id (`str`):
                The owner user id.

        Returns:
            `list[KnowledgeBaseRecord]`:
                All knowledge base records belonging to the user.
        """
        return await self._manager.list_knowledge_bases(user_id)

    async def list_knowledge_base_views(
        self,
        user_id: str,
        *,
        knowledge_base_id: str | None = None,
        name: str | None = None,
        page: int = 1,
        page_size: int = 30,
        orderby: str = "create_time",
        desc: bool = True,
    ) -> tuple[list[KnowledgeBaseView], int]:
        """List visible knowledge bases as enriched, paginated views.

        Serves the single list endpoint that doubles as "get one"
        (filter by ``knowledge_base_id``), mirroring RAGFlow's dataset
        API. Filtering and ordering happen before the page is cut;
        the returned total counts the filtered set so clients can
        compute page numbers.

        Only the served page is enriched: per view, the documents are
        read once from storage to derive the counts, and the embedding
        credential is resolved against the owner so shared viewers see
        its display name too.

        Args:
            user_id (`str`):
                The viewer user id — own + shared knowledge bases.
            knowledge_base_id (`str | None`, optional):
                Filter down to one knowledge base by id.
            name (`str | None`, optional):
                Case-insensitive substring filter on the display name.
            page (`int`, defaults to ``1``):
                1-based page number.
            page_size (`int`, defaults to ``30``):
                Knowledge bases per page.
            orderby (`str`, defaults to ``"create_time"``):
                Sort key — ``"create_time"`` or ``"update_time"``.
            desc (`bool`, defaults to ``True``):
                Sort newest first.

        Returns:
            `tuple[list[KnowledgeBaseView], int]`:
                The requested page of views and the filtered total.
        """
        views = await self._access.list_resource(
            user_id,
            ResourceKind.KNOWLEDGE_BASE,
        )
        if knowledge_base_id is not None:
            views = [view for view in views if view.id == knowledge_base_id]
        if name is not None:
            needle = name.lower()
            views = [view for view in views if needle in view.name.lower()]
        total = len(views)

        # The id breaks ties: storage returns rows in an undefined
        # order and MySQL DATETIME has no sub-second precision, so
        # records created together would otherwise shuffle between
        # requests and make pages drop or repeat rows.
        sort_key = "updated_at" if orderby == "update_time" else "created_at"
        views.sort(
            key=lambda view: (getattr(view, sort_key), view.id),
            reverse=desc,
        )
        page_views = views[(page - 1) * page_size : page * page_size]

        credential_names: dict[tuple[str, str], str | None] = {}
        for view in page_views:
            documents = await self._storage.list_knowledge_documents(
                view.owner_id,
                view.id,
            )
            view.document_count = len(documents)
            view.chunk_count = sum(
                document.data.chunk_count for document in documents
            )
            view.status_counts = KnowledgeBaseStatusCounts.model_validate(
                Counter(document.status for document in documents),
            )

            cache_key = (
                view.owner_id,
                view.embedding_model_config.credential_id,
            )
            if cache_key not in credential_names:
                credential = await self._storage.get_credential(*cache_key)
                credential_names[cache_key] = (
                    credential.data.get("name")
                    if credential is not None
                    else None
                )
            view.credential_name = credential_names[cache_key]

        return page_views, total

    async def update_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> "KnowledgeBaseRecord":
        """Update mutable fields on a knowledge base, raising 404 if absent.

        Only ``name`` and ``description`` are mutable.  The embedding
        model configuration is pinned at creation time.
        """
        owner_id = await self._require_edit(user_id, knowledge_base_id)
        record = await self._manager.update_knowledge_base(
            user_id=owner_id,
            knowledge_base_id=knowledge_base_id,
            name=name,
            description=description,
        )
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Knowledge base {knowledge_base_id!r} not found.",
            )
        return record

    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> None:
        """Delete a knowledge base, raising 404 if absent.

        Documents under the KB are cascade-deleted at the storage
        layer; blob files referenced by those records are released
        best-effort here so disk space is reclaimed even though the
        manager + storage cascade would otherwise orphan them.
        """
        owner_id = await self._require_edit(user_id, knowledge_base_id)
        documents = await self._storage.list_knowledge_documents(
            owner_id,
            knowledge_base_id,
        )
        for document in documents:
            await self._delete_blob_quietly(document.data.blob_uri)

        deleted = await self._manager.delete_knowledge_base(
            owner_id,
            knowledge_base_id,
        )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Knowledge base {knowledge_base_id!r} not found.",
            )

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def register_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        filename: str,
        stream: IO[bytes],
        size: int,
        content_type: str | None = None,
    ) -> KnowledgeDocumentRecord:
        """Persist an uploaded document and enqueue it for indexing.

        Streams ``stream`` into the blob store (so the bytes never
        live fully in memory), records a ``pending`` document, and
        pushes an index-task entry onto the message bus.  Returns
        immediately — a worker (in-process or dedicated) takes over
        from here and the client tracks progress via
        :meth:`get_document_status`.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The target knowledge base id.
            filename (`str`):
                The original filename.
            stream (`IO[bytes]`):
                A synchronous binary stream — typically
                ``UploadFile.file`` from FastAPI.
            size (`int`):
                Byte length declared by the uploader.  Persisted on
                the record for the UI; not authoritative.
            content_type (`str | None`, optional):
                IANA media type; ``None`` lets the worker fall back
                to a filename guess at processing time.

        Returns:
            `KnowledgeDocumentRecord`:
                The persisted record (``status='pending'``) with the
                final ``blob_uri`` filled in.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base does not exist.
        """
        # Authorise before touching the blob store: raising after a
        # write would leave the blob orphaned. Uploading a document is
        # a mutation, so require edit permission.
        owner_id = await self._require_edit(user_id, knowledge_base_id)

        document_id = uuid.uuid4().hex
        blob_uri = await self._blob_store.write_stream(
            key=f"kb/{knowledge_base_id}/{document_id}",
            stream=stream,
        )

        record = KnowledgeDocumentRecord(
            id=document_id,
            user_id=owner_id,
            knowledge_base_id=knowledge_base_id,
            data=KnowledgeDocumentData(
                filename=filename,
                size=size,
                content_type=content_type,
                blob_uri=blob_uri,
            ),
        )
        try:
            stored = await self._storage.upsert_knowledge_document(
                owner_id,
                record,
            )
        except Exception:
            # Storage write failed — drop the blob so the orphan
            # sweeper doesn't later see a referenced-by-nobody file.
            await self._delete_blob_quietly(blob_uri)
            raise

        await enqueue_index_task(
            self._bus,
            user_id=owner_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        return stored

    async def list_documents(
        self,
        user_id: str,
        knowledge_base_id: str,
        *,
        document_id: str | None = None,
        keywords: str | None = None,
        doc_status: str | None = None,
        page: int = 1,
        page_size: int = 30,
        orderby: str = "create_time",
        desc: bool = True,
    ) -> tuple[list[KnowledgeDocumentRecord], int]:
        """List documents of a knowledge base, filtered and paginated.

        Service-mode source of truth: reads from storage, NOT the
        vector store.  Documents in ``pending`` / ``parsing`` /
        ``chunking`` / ``indexing`` / ``error`` show up here even
        though they have no chunks in the vector store yet.

        Args:
            user_id (`str`):
                The viewer user id — can be the owner or a viewer
                granted access through the resource access policy.
            knowledge_base_id (`str`):
                The target knowledge base id.
            document_id (`str | None`, optional):
                Filter down to one document by id.
            keywords (`str | None`, optional):
                Case-insensitive substring filter on the filename.
            doc_status (`str | None`, optional):
                Filter by indexing status (``pending`` / ``parsing`` /
                ``chunking`` / ``indexing`` / ``ready`` / ``error``).
            page (`int`, defaults to ``1``):
                1-based page number.
            page_size (`int`, defaults to ``30``):
                Documents per page.
            orderby (`str`, defaults to ``"create_time"``):
                Sort key — ``"create_time"`` or ``"update_time"``.
            desc (`bool`, defaults to ``True``):
                Sort newest first.

        Returns:
            `tuple[list[KnowledgeDocumentRecord], int]`:
                The requested page of records and the filtered total.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base is not visible to the
                caller.
        """
        record = await self._access.resolve_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        records = await self._storage.list_knowledge_documents(
            record.user_id,
            knowledge_base_id,
        )
        if document_id is not None:
            records = [r for r in records if r.id == document_id]
        if keywords is not None:
            needle = keywords.lower()
            records = [r for r in records if needle in r.data.filename.lower()]
        if doc_status is not None:
            records = [r for r in records if r.status == doc_status]
        total = len(records)

        # See ``list_knowledge_base_views`` — the id breaks timestamp
        # ties so pages stay stable; a batch upload lands within one
        # MySQL DATETIME tick.
        sort_key = "updated_at" if orderby == "update_time" else "created_at"
        records.sort(
            key=lambda record: (getattr(record, sort_key), record.id),
            reverse=desc,
        )
        return records[(page - 1) * page_size : page * page_size], total

    async def get_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_ids: list[str],
    ) -> list[KnowledgeDocumentRecord]:
        """Batch-fetch documents for status polling.

        The endpoint backing this method accepts a comma-separated list
        of ids so the front-end can ask "what's the state of these N
        in-flight uploads" in a single round-trip.  Records that do
        not exist or do not belong to the user are silently skipped —
        the front-end may legitimately ask about a document that was
        deleted between two polls.

        Args:
            user_id (`str`):
                The viewer user id.
            knowledge_base_id (`str`):
                The target knowledge base id.
            document_ids (`list[str]`):
                Document ids to look up.

        Returns:
            `list[KnowledgeDocumentRecord]`:
                One record per matched id; missing ids omitted.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base is not visible to the
                caller.
        """
        record = await self._access.resolve_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        records: list[KnowledgeDocumentRecord] = []
        for document_id in document_ids:
            record_doc = await self._storage.get_knowledge_document(
                record.user_id,
                knowledge_base_id,
                document_id,
            )
            if record_doc is not None:
                records.append(record_doc)
        return records

    async def get_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeDocumentRecord:
        """Fetch one document record, or 404.

        Args:
            user_id (`str`):
                The viewer user id — owner or shared reader.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document to fetch.

        Returns:
            `KnowledgeDocumentRecord`:
                The matching record.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base is not visible to the
                caller or the document does not exist in it.
        """
        record = await self._access.resolve_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        document = await self._storage.get_knowledge_document(
            record.user_id,
            knowledge_base_id,
            document_id,
        )
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {document_id} not found.",
            )
        return document

    async def list_document_chunks(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        *,
        page: int = 1,
        page_size: int = 30,
    ) -> tuple[list[Chunk], int]:
        """List one page of a document's chunks in ``chunk_index`` order.

        ``page`` / ``page_size`` translate to the dense-``chunk_index``
        slice ``[(page - 1) * page_size, page * page_size)`` — stable
        under concurrent writes because chunk indices never move.  The
        total comes from the document record's ``chunk_count`` rather
        than a vector-store count, so a document that is still
        indexing reports the chunks persisted so far with a total
        of ``0`` until it turns ``ready``.

        Args:
            user_id (`str`):
                The viewer user id — owner or shared reader.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document whose chunks should be listed.
            page (`int`, defaults to ``1``):
                1-based page number.
            page_size (`int`, defaults to ``30``):
                Chunks per page.

        Returns:
            `tuple[list[Chunk], int]`:
                The page of chunks (``chunk_index`` ascending) and the
                document's total chunk count.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base or document is not
                visible to the caller; ``501`` if the configured
                vector store does not implement chunk listing.
        """
        document = await self.get_document(
            user_id,
            knowledge_base_id,
            document_id,
        )
        knowledge = await self._resolve_knowledge(user_id, knowledge_base_id)
        try:
            chunks = await knowledge.list_chunks(
                document_id,
                offset=(page - 1) * page_size,
                limit=page_size,
            )
        except NotImplementedError as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    "The configured vector store does not support "
                    "chunk listing."
                ),
            ) from exc
        return chunks, document.data.chunk_count

    async def stream_document_content(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> tuple[KnowledgeDocumentRecord, int | None, AsyncIterator[bytes]]:
        """Open the original uploaded file of a document for streaming.

        The bytes come straight from the blob store — the same blob
        the index worker parsed — in bounded 1 MiB chunks so a large
        download never holds the whole file in the API process.

        The size is measured on the blob rather than read off the
        record: ``data.size`` is what the upload request happened to
        carry, and a ``Content-Length`` that disagrees with the body
        breaks the response.

        Args:
            user_id (`str`):
                The viewer user id — owner or shared reader.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document whose original file should be streamed.

        Returns:
            `tuple[KnowledgeDocumentRecord, int | None, AsyncIterator[bytes]]`:
                The document record (for the filename / content-type
                headers), the blob's measured byte length — ``None``
                when the backend cannot measure it — and a lazy byte
                iterator that opens the blob on first pull.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base or document is not
                visible to the caller, or the underlying blob is gone
                (legacy data, switched blob backend).
        """
        document = await self.get_document(
            user_id,
            knowledge_base_id,
            document_id,
        )
        blob_uri = document.data.blob_uri
        try:
            # A measured size doubles as proof the blob is there, so the
            # extra existence check only runs for backends that cannot
            # measure.
            size = await self._blob_store.size(blob_uri)
            available = size is not None or await self._blob_store.exists(
                blob_uri,
            )
        except ValueError:
            # URI scheme unknown to this backend (e.g. records written
            # by a local:// deployment now running on s3://).
            size, available = None, False
        if not available:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The original file is no longer available.",
            )

        async def _iter_content() -> AsyncIterator[bytes]:
            async with self._blob_store.open(blob_uri) as fp:
                while True:
                    data = await fp.read(1 << 20)
                    if not data:
                        break
                    yield data

        return document, size, _iter_content()

    async def delete_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Remove a document end-to-end: vector store, record, blob.

        Order is chosen so that a crash mid-way always leaves a
        recoverable state:

        1. Vector store delete (idempotent — re-deleting an already
           empty document_id is harmless).
        2. Storage record delete.
        3. Blob delete (idempotent).

        A failure at step 1 surfaces as an exception to the caller and
        the record + blob are left untouched, so a retry sees the same
        state.  Failures at steps 2/3 leave a small amount of orphan
        data but the user-visible deletion has already succeeded from
        the vector store's point of view.

        Args:
            user_id (`str`):
                The viewer user id — owner or shared editor.
            knowledge_base_id (`str`):
                The target knowledge base id.
            document_id (`str`):
                The document to delete.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base is not visible to the
                caller; ``403`` if visible but not editable.
        """
        owner_id = await self._require_edit(user_id, knowledge_base_id)
        record = await self._storage.get_knowledge_document(
            owner_id,
            knowledge_base_id,
            document_id,
        )
        if record is None:
            # Idempotent: KB exists (edit check succeeded) but the
            # document is already gone.
            return

        knowledge = await self._resolve_knowledge(user_id, knowledge_base_id)
        await knowledge.delete_document(document_id)
        await self._storage.delete_knowledge_document(
            owner_id,
            knowledge_base_id,
            document_id,
        )
        await self._delete_blob_quietly(record.data.blob_uri)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        user_id: str,
        knowledge_base_id: str,
        query: str,
        top_k: int = 5,
    ) -> "list[VectorSearchResult]":
        """Search a knowledge base by text query.

        Args:
            user_id (`str`):
                The viewer user id — owner or shared reader.
            knowledge_base_id (`str`):
                The knowledge base to search.
            query (`str`):
                The natural-language query.
            top_k (`int`, defaults to ``5``):
                Maximum number of results.

        Returns:
            `list[VectorSearchResult]`:
                The top hits ordered by descending similarity score.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base is not visible to the
                caller.
        """
        knowledge = await self._resolve_knowledge(user_id, knowledge_base_id)
        return await knowledge.search(queries=[query], top_k=top_k)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _require_edit(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> str:
        """Ensure the caller can mutate this KB; return the *owner* id.

        Every KB mutation writes back through the owner's storage key,
        so a shared editor's request must be resolved to the owner id
        before touching storage. Raises ``404`` when the KB is not
        visible to ``user_id`` and ``403`` when it is visible but only
        readable.
        """
        owner_id, _ = await self._access.resolve_for_edit(
            user_id,
            ResourceKind.KNOWLEDGE_BASE,
            knowledge_base_id,
        )
        return owner_id

    async def _resolve_knowledge(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> "object":
        """Resolve a :class:`KnowledgeBase` for a viewer.

        Rewrites ``user_id`` to the owning user id when the caller is a
        shared reader/editor, so the manager reads the correct
        collection metadata from storage. ``KnowledgeBaseNotFoundError``
        is translated to ``404``.
        """
        record = await self._access.resolve_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        try:
            return await self._manager.get_knowledge(
                record.user_id,
                knowledge_base_id,
            )
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    async def _delete_blob_quietly(self, blob_uri: str) -> None:
        """Best-effort blob delete — swallow backend errors.

        Treated as cleanup: if the blob store is unavailable the
        record/vector-store state is still consistent and a future
        sweep can reclaim the disk space.  Surface only via logs.
        """
        try:
            await self._blob_store.delete(blob_uri)
        except Exception:  # noqa: BLE001 — cleanup only
            logger.exception(
                "Failed to delete blob %s",
                blob_uri,
            )
