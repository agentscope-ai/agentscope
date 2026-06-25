# -*- coding: utf-8 -*-
"""Background indexing pipeline for one knowledge document.

The :class:`IndexWorker` owns the post-upload half of the document
lifecycle.  Given a ``document_id`` it:

1. acquires the processing lease via storage CAS (so only one worker
   in the cluster handles the document at a time);
2. reads the bytes back from the blob store (streamed);
3. routes to a parser by IANA media type;
4. chunks the resulting sections;
5. embeds + writes to the vector store through
   :class:`~agentscope.app.knowledge_base_manager.Knowledge`;
6. transitions the status through ``parsing → chunking → indexing →
   ready`` (or ``error``) on the way.

The worker is intentionally embeddable: a single instance can live
inside the API process (embedded deployment) or inside a dedicated
worker process (dedicated deployment).  Coordination across workers
is done entirely through the storage lease — workers do not need to
know about each other.
"""
import asyncio
import mimetypes
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING

from ..._logging import logger

if TYPE_CHECKING:
    from ..blob_store import BlobStoreBase
    from ..knowledge_base_manager import KnowledgeBaseManagerBase
    from ..storage import StorageBase
    from ...rag import ChunkerBase, ParserBase, Section

# Read blob bytes in chunks bounded so the worker never holds the whole
# file in memory at once even when the parser is byte-oriented.
_READ_CHUNK = 1 << 20  # 1 MiB


class IndexWorker:
    """Drive one document through parse → chunk → index.

    Multiple invocations of :meth:`process` are run concurrently up to
    a per-worker semaphore.  The semaphore protects shared resources
    that scale with the number of in-flight parses (memory for big
    PDFs, embedding API rate budget), while the lease CAS in storage
    protects against the *cross-worker* version of the same race.
    """

    def __init__(
        self,
        storage: "StorageBase",
        blob_store: "BlobStoreBase",
        knowledge_base_manager: "KnowledgeBaseManagerBase",
        parsers: "list[ParserBase]",
        chunker: "ChunkerBase",
        node_id: str,
        max_concurrency: int = 4,
        lease_ttl: timedelta = timedelta(seconds=90),
        parser_executor: ProcessPoolExecutor | None = None,
    ) -> None:
        """Initialize the worker.

        Args:
            storage (`StorageBase`):
                Document records, lease, status.
            blob_store (`BlobStoreBase`):
                Source of the document bytes.
            knowledge_base_manager (`KnowledgeBaseManagerBase`):
                Resolves the :class:`Knowledge` runtime for embedding
                and vector store writes.
            parsers (`list[ParserBase]`):
                Parsers indexed by ``supported_media_types``.  Same
                registry the upload service uses, passed in by DI.
            chunker (`ChunkerBase`):
                The shared chunker.
            node_id (`str`):
                Stable identifier for this worker process.  Used as
                ``processing_node`` on the lease so the sweeper can
                tell whose work expired.  Typically
                ``f"{hostname}:{pid}:{uuid}"``.
            max_concurrency (`int`, defaults to ``4``):
                Maximum number of documents processed concurrently by
                this worker.  Higher values trade memory for
                throughput; tune per embedding-API rate limits and
                per-document parse cost.
            lease_ttl (`timedelta`, defaults to ``90s``):
                How long a single processing lease lives.  The worker
                renews periodically so long-running parses do not
                trip the sweeper.
            parser_executor (`ProcessPoolExecutor | None`, optional):
                Process pool used to off-load CPU-intensive parses
                (PDF, Office).  ``None`` runs parses in the event-loop
                thread, which is fine for plain text but unsafe for
                third-party byte-oriented parsers.  Injected so a
                single pool can be shared across the app (built in
                lifespan).
        """
        self._storage = storage
        self._blob_store = blob_store
        self._manager = knowledge_base_manager
        self._parsers_by_media_type: dict[str, "ParserBase"] = {}
        for parser in parsers:
            for media_type in parser.supported_media_types:
                self._parsers_by_media_type[media_type] = parser
        self._chunker = chunker
        self._node_id = node_id
        self._lease_ttl = lease_ttl
        self._sem = asyncio.Semaphore(max_concurrency)
        self._parser_executor = parser_executor
        # Renewal cadence: refresh while there is still half the lease
        # left so a one-cycle missed renewal doesn't drop the lease.
        self._renew_interval = max(lease_ttl / 2, timedelta(seconds=5))

    async def process(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Run the full indexing pipeline for one document.

        Steps:

        1. **Lease** — CAS-acquire the processing lease; bail if some
           other worker already holds it (duplicate dispatch / sweep).
        2. **Throttle** — wait on the per-worker semaphore so the
           number of in-flight parses stays bounded.
        3. **Pipeline** — parse → chunk → embed + write vector store,
           updating status before each phase.  A background heartbeat
           keeps the lease alive while parsing runs.
        4. **Finalise** — on success mark ``ready`` with the final
           chunk count; on failure mark ``error`` with a sanitised
           message.  The lease is released regardless.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document to process.
        """
        acquired = await self._storage.acquire_knowledge_document_lease(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            processing_node=self._node_id,
            lease_ttl=self._lease_ttl,
        )
        if not acquired:
            logger.debug(
                "Skipping %s — another worker holds the lease.",
                document_id,
            )
            return

        heartbeat = asyncio.create_task(
            self._heartbeat(user_id, knowledge_base_id, document_id),
            name=f"lease-renew:{document_id}",
        )
        try:
            async with self._sem:
                await self._run_pipeline(
                    user_id,
                    knowledge_base_id,
                    document_id,
                )
        except asyncio.CancelledError:
            # Shutdown path — let the sweeper redispatch once the
            # lease expires; do NOT update status because the work
            # may still be picked up cleanly.
            raise
        except Exception as exc:  # noqa: BLE001 — terminal error sink
            await self._mark_error(
                user_id,
                knowledge_base_id,
                document_id,
                exc,
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await self._storage.release_knowledge_document_lease(
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                processing_node=self._node_id,
            )

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Walk the document through parse → chunk → index."""
        record = await self._storage.get_knowledge_document(
            user_id,
            knowledge_base_id,
            document_id,
        )
        if record is None:
            logger.warning(
                "Document %s vanished before processing.",
                document_id,
            )
            return

        data = record.data
        media_type = (
            data.content_type or mimetypes.guess_type(data.filename)[0]
        )
        if not media_type:
            raise ValueError(
                f"Cannot determine media type for {data.filename!r}.",
            )
        parser = self._parsers_by_media_type.get(media_type)
        if parser is None:
            raise ValueError(
                f"No parser registered for media type {media_type!r}.",
            )

        # ---- parsing ----
        await self._storage.update_knowledge_document_status(
            user_id,
            knowledge_base_id,
            document_id,
            "parsing",
        )
        file_bytes = await self._read_blob(data.blob_uri)
        sections = await self._parse(parser, file_bytes, data.filename)

        # ---- chunking ----
        await self._storage.update_knowledge_document_status(
            user_id,
            knowledge_base_id,
            document_id,
            "chunking",
        )
        chunks = await self._chunker.chunk(sections)

        # ---- indexing ----
        await self._storage.update_knowledge_document_status(
            user_id,
            knowledge_base_id,
            document_id,
            "indexing",
        )
        knowledge = await self._manager.get_knowledge(
            user_id,
            knowledge_base_id,
        )
        await knowledge.insert_document(
            chunks=chunks,
            document_id=document_id,
            document_metadata={
                "filename": data.filename,
                "media_type": media_type,
                "size_bytes": data.size,
            },
        )

        # ---- ready ----
        await self._storage.update_knowledge_document_status(
            user_id,
            knowledge_base_id,
            document_id,
            "ready",
            chunk_count=len(chunks),
        )

    async def _parse(
        self,
        parser: "ParserBase",
        file_bytes: bytes,
        filename: str,
    ) -> "list[Section]":
        """Run the parser, optionally on the process pool."""
        if self._parser_executor is None:
            return await parser.parse(file_bytes, filename)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._parser_executor,
            _run_parser_sync,
            parser,
            file_bytes,
            filename,
        )

    async def _read_blob(self, blob_uri: str) -> bytes:
        """Stream the blob into memory in bounded chunks.

        We buffer the whole file before handing it to the parser
        because today's parser API is byte-oriented (``parse(file:
        bytes, filename: str)``).  The read loop still avoids large
        single allocations and gives us a single place to upgrade to a
        true streaming parser API later — only this method needs to
        change.
        """
        buffer = bytearray()
        async with self._blob_store.open(blob_uri) as fp:
            while True:
                chunk = await fp.read(_READ_CHUNK)
                if not chunk:
                    break
                buffer.extend(chunk)
        return bytes(buffer)

    # ------------------------------------------------------------------
    # Lease heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Renew the lease in the background while processing runs.

        Cancels itself silently when the surrounding pipeline finishes
        — the cancel happens in :meth:`process` after the try/finally
        body has run.  A renewal that fails (e.g. another worker
        stole the lease after a sweep) is logged; the pipeline finds
        out via its own write contention and bails on the next
        ``update_knowledge_document_status``.
        """
        interval_seconds = self._renew_interval.total_seconds()
        while True:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                return
            ok = await self._storage.renew_knowledge_document_lease(
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                processing_node=self._node_id,
                lease_ttl=self._lease_ttl,
            )
            if not ok:
                logger.warning(
                    "Lost lease on %s while processing.",
                    document_id,
                )
                return

    # ------------------------------------------------------------------
    # Error sink
    # ------------------------------------------------------------------

    async def _mark_error(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        exc: BaseException,
    ) -> None:
        """Persist a sanitised error and mark the document failed."""
        logger.exception(
            "Indexing failed for %s/%s",
            knowledge_base_id,
            document_id,
            exc_info=exc,
        )
        message = _sanitise_error(exc)
        try:
            await self._storage.update_knowledge_document_status(
                user_id,
                knowledge_base_id,
                document_id,
                "error",
                error=message,
            )
        except Exception:  # noqa: BLE001 — last-resort log
            logger.exception(
                "Failed to persist error status for %s",
                document_id,
            )


# ----------------------------------------------------------------------
# Module-level helpers (picklable for ProcessPoolExecutor)
# ----------------------------------------------------------------------


def _run_parser_sync(
    parser: "ParserBase",
    file_bytes: bytes,
    filename: str,
) -> "list[Section]":
    """Run an async parser to completion inside a sync executor."""
    return asyncio.run(parser.parse(file_bytes, filename))


def _sanitise_error(exc: BaseException) -> str:
    """Reduce an exception to a single user-facing line.

    Only the exception class name + first line of its message are
    kept — stack traces and filesystem paths stay inside the worker
    log and out of the user-visible record.
    """
    raw = str(exc) or exc.__class__.__name__
    first_line = raw.splitlines()[0].strip()
    cls = exc.__class__.__name__
    if not first_line:
        return cls
    return f"{cls}: {first_line[:240]}"
