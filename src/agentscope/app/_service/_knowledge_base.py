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
from typing import IO, TYPE_CHECKING

from fastapi import HTTPException, status

from ..rag.knowledge_base_manager import (
    DimensionPolicyError,
    KnowledgeBaseNotFoundError,
)
from ..storage import (
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
)
from ..._logging import logger
from .._bus_ops import enqueue_index_task

if TYPE_CHECKING:
    from ..rag.blob_store import BlobStoreBase
    from ..rag.knowledge_base_manager import KnowledgeBaseManagerBase
    from ..message_bus import MessageBus
    from ..storage import (
        EmbeddingModelConfig,
        KnowledgeBaseRecord,
        StorageBase,
    )
    from ...rag import VectorSearchResult


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
        """
        self._storage = storage
        self._manager = knowledge_base_manager
        self._blob_store = blob_store
        self._bus = message_bus

    # ------------------------------------------------------------------
    # Knowledge base CRUD
    # ------------------------------------------------------------------

    async def create_knowledge_base(
        self,
        user_id: str,
        name: str,
        description: str,
        embedding_model_config: "EmbeddingModelConfig",
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

        Returns:
            `KnowledgeBaseRecord`:
                The newly persisted record.

        Raises:
            `HTTPException`:
                ``409`` when the requested embedding dimension
                violates the manager's dimension policy.
        """
        try:
            return await self._manager.create_knowledge_base(
                user_id=user_id,
                name=name,
                description=description,
                embedding_model_config=embedding_model_config,
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
        record = await self._manager.update_knowledge_base(
            user_id=user_id,
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
        documents = await self._storage.list_knowledge_documents(
            user_id,
            knowledge_base_id,
        )
        for document in documents:
            await self._delete_blob_quietly(document.data.blob_uri)

        deleted = await self._manager.delete_knowledge_base(
            user_id,
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
        # write would leave the blob orphaned.
        await self._authorise_kb(user_id, knowledge_base_id)

        document_id = uuid.uuid4().hex
        blob_uri = await self._blob_store.write_stream(
            key=f"kb/{knowledge_base_id}/{document_id}",
            stream=stream,
        )

        record = KnowledgeDocumentRecord(
            id=document_id,
            user_id=user_id,
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
                user_id,
                record,
            )
        except Exception:
            # Storage write failed — drop the blob so the orphan
            # sweeper doesn't later see a referenced-by-nobody file.
            await self._delete_blob_quietly(blob_uri)
            raise

        await enqueue_index_task(
            self._bus,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        return stored

    async def list_documents(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> list[KnowledgeDocumentRecord]:
        """List every document registered against a knowledge base.

        Service-mode source of truth: reads from storage, NOT the
        vector store.  Documents in ``pending`` / ``parsing`` /
        ``chunking`` / ``indexing`` / ``error`` show up here even
        though they have no chunks in the vector store yet.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The target knowledge base id.

        Returns:
            `list[KnowledgeDocumentRecord]`:
                Every document registered against the knowledge base,
                in unspecified order.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base does not exist.
        """
        await self._authorise_kb(user_id, knowledge_base_id)
        return await self._storage.list_knowledge_documents(
            user_id,
            knowledge_base_id,
        )

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
                The owner user id.
            knowledge_base_id (`str`):
                The target knowledge base id.
            document_ids (`list[str]`):
                Document ids to look up.

        Returns:
            `list[KnowledgeDocumentRecord]`:
                One record per matched id; missing ids omitted.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base does not exist.
        """
        await self._authorise_kb(user_id, knowledge_base_id)
        records: list[KnowledgeDocumentRecord] = []
        for document_id in document_ids:
            record = await self._storage.get_knowledge_document(
                user_id,
                knowledge_base_id,
                document_id,
            )
            if record is not None:
                records.append(record)
        return records

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
                The owner user id.
            knowledge_base_id (`str`):
                The target knowledge base id.
            document_id (`str`):
                The document to delete.

        Raises:
            `HTTPException`:
                ``404`` if the knowledge base does not exist.
        """
        record = await self._storage.get_knowledge_document(
            user_id,
            knowledge_base_id,
            document_id,
        )
        if record is None:
            # 404 if the KB does not exist, otherwise treat the
            # missing document as already-deleted (idempotent).
            await self._authorise_kb(user_id, knowledge_base_id)
            return

        knowledge = await self._resolve_knowledge(user_id, knowledge_base_id)
        await knowledge.delete_document(document_id)
        await self._storage.delete_knowledge_document(
            user_id,
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
                The owner user id.
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
                ``404`` if the knowledge base does not exist.
        """
        knowledge = await self._resolve_knowledge(user_id, knowledge_base_id)
        return await knowledge.search(queries=[query], top_k=top_k)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _authorise_kb(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> "KnowledgeBaseRecord":
        """Look the KB record up so we can 404 cleanly.

        The check is intentionally separate from :meth:`_resolve_knowledge`
        because document-level endpoints (list / delete) need to refuse
        unknown KBs without paying the embedding-model construction cost
        that :meth:`_resolve_knowledge` triggers.
        """
        record = await self._storage.get_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Knowledge base {knowledge_base_id!r} not found.",
            )
        return record

    async def _resolve_knowledge(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> "object":
        """Resolve a :class:`KnowledgeBase` and translate not-found to 404."""
        try:
            return await self._manager.get_knowledge(
                user_id,
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
