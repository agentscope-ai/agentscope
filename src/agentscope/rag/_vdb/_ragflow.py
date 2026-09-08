# -*- coding: utf-8 -*-
"""RAGFlow implementation of the vector store backend.

Built on the official ``ragflow-sdk`` package.  Each knowledge base maps to
one RAGFlow **dataset**.  Each AgentScope :class:`~agentscope.rag.Chunk` is
uploaded as a **separate** text file whose filename carries a reversible
Base64-encoded ``document_id`` and whose first line embeds a JSON sidecar
(``# agentscope: {...}``) with the serialised chunk metadata needed to
reconstruct results, scope deletions, and apply ``metadata_filter``.

.. note:: The ``ragflow-sdk`` package is required. Install it with
    ``pip install ragflow-sdk``, or ``pip install agentscope[ragflow]``.

.. code-block:: python

    store = RAGFlowStore(
        api_key="ragflow-...",
        base_url="http://localhost:9380",
    )

    async with store:
        await store.create_collection("kb-1", dimensions=768)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tempfile
from typing import TYPE_CHECKING, Any

from .._document import Chunk
from ...message import TextBlock
from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)

if TYPE_CHECKING:
    from ragflow_sdk import RAGFlow, DataSet

# Prefix inserted at the start of every uploaded file to store chunk
# metadata.  Each file carries exactly one Chunk, so the sidecar is a
# single JSON object (not a list).  Format:
#   # agentscope: <compact-json>
_SIDECAR_PREFIX = "# agentscope: "
_SIDECAR_RE = re.compile(r"^# agentscope: (.+)$", re.MULTILINE)


class RAGFlowStore(VectorStoreBase):
    """Vector store backend backed by `RAGFlow <https://ragflow.io>`_.

    Each knowledge base maps to one RAGFlow dataset.  Because RAGFlow is a
    full RAG engine that manages its own embeddings and chunking, the vectors
    passed via :class:`VectorRecord` are **not** used for retrieval; instead
    RAGFlow's native hybrid (keyword + vector) search is used.

    .. note::

        :meth:`search` uses RAGFlow's ``retrieve`` API, which performs
        hybrid search internally.  The ``query_vector`` parameter is
        accepted for interface compatibility but is not used — RAGFlow
        generates its own query embedding from its internal text index.
        Retrieval may be less precise than a pure vector store; consider
        Qdrant or Milvus Lite when exact vector similarity is critical.

    .. code-block:: python

        store = RAGFlowStore(
            api_key="ragflow-...",
            base_url="http://localhost:9380",
        )

        async with store:
            await store.create_collection("kb-1", dimensions=768)
            await store.insert("kb-1", records)
            results = await store.search("kb-1", query_vector=[...])
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:9380",
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the RAGFlow vector store.

        Args:
            api_key (`str`):
                The RAGFlow API key.
            base_url (`str`, defaults to ``"http://localhost:9380"``):
                The base URL of the RAGFlow server.
            client_kwargs (`dict[str, Any] | None`, optional):
                Extra keyword arguments forwarded to the
                :class:`~ragflow_sdk.RAGFlow` constructor.
        """
        self._api_key = api_key
        self._base_url = base_url
        self._client_kwargs = client_kwargs or {}
        self._client: "RAGFlow | None" = None

    def get_client(self) -> "RAGFlow":
        """Lazily create and cache the RAGFlow client.

        Returns:
            `RAGFlow`: The shared RAGFlow client instance.
        """
        if self._client is None:
            from ragflow_sdk import RAGFlow  # noqa: PLC0415

            self._client = RAGFlow(
                api_key=self._api_key,
                base_url=self._base_url,
                **self._client_kwargs,
            )
        return self._client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context — RAGFlow SDK is stateless, no-op."""
        self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_dataset_by_name(
        self,
        name: str,
    ) -> "DataSet | None":
        """Look up a dataset by name.

        Iterates the dataset list (paged) returned by the SDK and returns
        the first match.

        Args:
            name: The dataset name to search for.

        Returns:
            The matching dataset, or ``None`` if not found.
        """
        client = self.get_client()
        page = 1
        while True:
            datasets = await asyncio.to_thread(
                client.list_datasets,
                page=page,
                page_size=100,
            )
            if not datasets:
                break
            for ds in datasets:
                if ds.name == name:
                    return ds
            if len(datasets) < 100:
                break
            page += 1
        return None

    @staticmethod
    def _make_filename(document_id: str) -> str:
        """Build an upload filename that reversibly carries the AgentScope
        ``document_id``.

        Uses URL-safe Base64 encoding so every ``document_id`` maps to a
        unique, round-trippable filename with no collisions.

        Args:
            document_id: The AgentScope source document identifier.

        Returns:
            A filename like ``agentscope_<b64>.txt``.
        """
        encoded = base64.urlsafe_b64encode(
            document_id.encode("utf-8"),
        ).decode("ascii")
        return f"agentscope_{encoded}.txt"

    @staticmethod
    def _parse_document_id_from_name(name: str) -> str | None:
        """Recover the AgentScope ``document_id`` from a RAGFlow document name.

        Args:
            name: The RAGFlow document name / title.

        Returns:
            The extracted document_id, or ``None``.
        """
        m = re.match(r"^agentscope_(.+)\.txt$", name)
        if not m:
            return None
        try:
            return base64.urlsafe_b64decode(
                m.group(1).encode("ascii"),
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    @staticmethod
    def _build_sidecar(record: VectorRecord) -> str:
        """Serialise a single chunk's metadata into a one-line JSON sidecar.

        Args:
            record: The record to encode.

        Returns:
            A JSON string (compact, one line).
        """
        return json.dumps(
            {
                "document_id": record.document_id,
                "chunk_index": record.chunk.chunk_index,
                "total_chunks": record.chunk.total_chunks,
                "source": record.chunk.source,
                "chunk": record.chunk.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _parse_sidecar(content: str) -> dict[str, Any] | None:
        """Extract the sidecar JSON from a single-chunk document's content.

        Args:
            content: The RAGFlow chunk / document text.

        Returns:
            Parsed sidecar dict, or ``None``.
        """
        m = _SIDECAR_RE.search(content)
        if not m:
            return None
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and "chunk" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    @staticmethod
    def _chunk_from_sidecar(content: str) -> Chunk | None:
        """Reconstruct a :class:`Chunk` from sidecar-embedded content.

        The returned chunk's ``metadata`` will include ``document_id``
        injected from the sidecar's top-level field so callers can
        recover the source document identity.

        Args:
            content: RAGFlow chunk text (may contain sidecar line).

        Returns:
            The reconstructed chunk, or ``None``.
        """
        data = RAGFlowStore._parse_sidecar(content)
        if data is None:
            return None
        chunk = Chunk.model_validate(data["chunk"])
        if "document_id" in data:
            chunk.metadata["document_id"] = data["document_id"]
        return chunk

    @staticmethod
    def _matches_metadata_filter(
        sidecar: dict[str, Any] | None,
        metadata_filter: dict[str, Any] | None,
    ) -> bool:
        """Check whether a sidecar entry matches a flat metadata filter.

        Args:
            sidecar: Parsed sidecar dict (or ``None``).
            metadata_filter: ``{key: value}`` filter (or ``None``).

        Returns:
            ``True`` if the entry matches or no filter is applied.
        """
        if not metadata_filter:
            return True
        if sidecar is None:
            return False
        chunk_meta = sidecar.get("chunk", {}).get("metadata", {})
        return all(chunk_meta.get(k) == v for k, v in metadata_filter.items())

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    async def create_collection(
        self,
        name: str,
        dimensions: int,
    ) -> None:
        """Create a new RAGFlow dataset.

        No-op if a dataset with the same name already exists.

        Args:
            name: The dataset name (typically the knowledge base ID).
            dimensions: Stored in dataset description for reference but
                **not** enforced (RAGFlow manages its own embedding
                dimensions internally).
        """
        existing = await self._get_dataset_by_name(name)
        if existing is not None:
            return
        await asyncio.to_thread(
            self.get_client().create_dataset,
            name=name,
            description=f"AgentScope KB | dimensions={dimensions}",
        )

    async def delete_collection(self, name: str) -> None:
        """Delete a dataset and all its data.

        Args:
            name: The dataset name to delete.
        """
        ds = await self._get_dataset_by_name(name)
        if ds is None:
            return
        await asyncio.to_thread(
            self.get_client().delete_datasets,
            ids=[ds.id],
        )

    async def has_collection(self, name: str) -> bool:
        """Check whether a dataset exists.

        Args:
            name: The dataset name to check.

        Returns:
            ``True`` if the dataset exists.
        """
        return await self._get_dataset_by_name(name) is not None

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a dataset.

        **Each chunk is uploaded as a separate file** so that every
        RAGFlow document carries its own sidecar — RAGFlow's internal
        re-chunking cannot strip metadata from one chunk and leave it on
        another.

        Newly uploaded documents are tracked and only they (not all
        pre-existing documents) are parsed after upload.

        Args:
            collection: The target dataset name.
            records: The records to insert.

        Raises:
            ValueError: If the collection does not exist.
        """
        if not records:
            return

        ds = await self._get_dataset_by_name(collection)
        if ds is None:
            raise ValueError(
                f"Collection '{collection}' not found. "
                "Call create_collection first.",
            )

        # Capture existing document count so we can identify newly
        # uploaded docs by their position in the list.
        existing = await asyncio.to_thread(
            ds.list_documents,
            page=1,
            page_size=1,
        )
        existing_count = len(existing) if existing else 0

        # Use a unique temp directory to avoid races on concurrent
        # inserts, then write each chunk to a file whose name carries
        # the Base64-encoded document_id.
        tmpdir = tempfile.mkdtemp(prefix="agentscope_")
        try:
            for rec in records:
                sidecar = self._build_sidecar(rec)
                payload = f"{_SIDECAR_PREFIX}{sidecar}\n{rec.chunk.content}"
                tmp_path = os.path.join(
                    tmpdir,
                    self._make_filename(rec.document_id),
                )
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(payload)
                await asyncio.to_thread(
                    ds.upload_documents,
                    [tmp_path],
                )
        finally:
            import shutil

            if os.path.isdir(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)

        # Parse only the newly uploaded documents.
        uploaded = await asyncio.to_thread(
            ds.list_documents,
            page=1,
            page_size=existing_count + len(records) + 1,
        )
        if uploaded:
            new_docs = uploaded[existing_count:]
            new_ids = [
                d.get("id") if isinstance(d, dict) else d.id for d in new_docs
            ]
            if new_ids:
                await asyncio.to_thread(
                    ds.async_parse_documents,
                    new_ids,
                )

    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document.

        Lists documents in the dataset, matches those whose filename
        encodes the given ``document_id`` (exact match), and deletes them.

        Args:
            collection: The target dataset name.
            document_id: The source document ID to remove.
        """
        ds = await self._get_dataset_by_name(collection)
        if ds is None:
            return

        target_filename = self._make_filename(document_id)
        to_delete: list[str] = []
        page = 1
        while True:
            docs = await asyncio.to_thread(
                ds.list_documents,
                page=page,
                page_size=100,
            )
            if not docs:
                break
            for d in docs:
                name = d.get("name", "") if isinstance(d, dict) else d.name
                if name == target_filename:
                    doc_id = d.get("id") if isinstance(d, dict) else d.id
                    to_delete.append(doc_id)
            if len(docs) < 100:
                break
            page += 1

        if to_delete:
            await asyncio.to_thread(
                ds.delete_documents,
                ids=to_delete,
            )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Search the dataset using RAGFlow's native hybrid retrieval.

        .. note::

            RAGFlow is a full RAG engine that generates its own query
            embeddings internally — it does not expose a "search by
            external vector" endpoint.  The ``query_vector`` parameter is
            accepted for interface compatibility but **is not used**.
            Retrieval quality depends on RAGFlow's configured embedding
            model and text index, not on the vectors computed at
            insertion time.

            ``metadata_filter`` is applied **client-side** after
            retrieval because RAGFlow's document metadata fields are not
            populated by this backend.

        Args:
            collection: The target dataset name.
            query_vector: **Not used.** Accepted for compatibility.
            top_k: Maximum number of results to return.
            metadata_filter: If provided, applied client-side to filter
                results by ``chunk.metadata``.

        Returns:
            Results ordered by descending similarity score.
        """
        ds = await self._get_dataset_by_name(collection)
        if ds is None:
            return []

        # RAGFlow's retrieve() performs hybrid (keyword + vector) search
        # over its internal index.  We pass an empty question so that
        # keyword matching against the indexed document text dominates;
        # the stored sidecar + chunk body are both indexed and searchable.
        raw_results = await asyncio.to_thread(
            self.get_client().retrieve,
            dataset_ids=[ds.id],
            question="",
            top_k=max(top_k * 3, 30),  # oversample; filter later
            similarity_threshold=0.0,
            vector_similarity_weight=0.0,
            keyword=True,
        )

        results: list[VectorSearchResult] = []
        for item in raw_results:
            score = (
                item.get("similarity", 0.0)
                if isinstance(item, dict)
                else getattr(item, "similarity", 0.0)
            )
            content = (
                item.get("content", "")
                if isinstance(item, dict)
                else getattr(item, "content", "")
            )
            doc_name = (
                item.get("document_name", "")
                if isinstance(item, dict)
                else getattr(item, "document_name", "")
            )

            # Apply client-side metadata_filter.
            sidecar = self._parse_sidecar(content)
            if not self._matches_metadata_filter(sidecar, metadata_filter):
                continue

            chunk = self._chunk_from_sidecar(content)
            resolved_id = (
                chunk.metadata.get("document_id", "")
                if chunk
                else (self._parse_document_id_from_name(doc_name) or doc_name)
            )

            results.append(
                VectorSearchResult(
                    score=score,
                    document_id=resolved_id,
                    chunk=chunk
                    or Chunk(
                        content=TextBlock(text=content),
                        source=doc_name,
                        chunk_index=0,
                        total_chunks=1,
                        metadata={"document_id": resolved_id},
                    ),
                ),
            )

        return results[:top_k]

    # ------------------------------------------------------------------
    # Document listing
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List all distinct source documents indexed in a dataset.

        Iterates RAGFlow documents, recovers the ``document_id`` from each
        document's filename, and aggregates into one
        :class:`DocumentSummary` per source document.  The real chunk
        count is read from the sidecar (which stores ``total_chunks``).

        ``metadata_filter`` is applied client-side against
        ``chunk.metadata``.

        Args:
            collection: The target dataset name.
            metadata_filter: If provided, restrict to records whose
                ``chunk.metadata`` matches every ``key == value`` pair.

        Returns:
            One summary per distinct ``document_id``.
        """
        ds = await self._get_dataset_by_name(collection)
        if ds is None:
            return []

        summaries: dict[str, DocumentSummary] = {}
        page = 1

        while True:
            docs = await asyncio.to_thread(
                ds.list_documents,
                page=page,
                page_size=100,
            )
            if not docs:
                break

            for d in docs:
                name = d.get("name", "") if isinstance(d, dict) else d.name
                content = (
                    d.get("content", "")
                    if isinstance(d, dict)
                    else getattr(d, "content", "")
                )

                resolved_id = self._parse_document_id_from_name(name) or name
                sidecar = self._parse_sidecar(content)

                # Apply metadata_filter client-side.
                if not self._matches_metadata_filter(
                    sidecar,
                    metadata_filter,
                ):
                    continue

                # Total chunks comes from the sidecar; fallback to 1.
                real_total = sidecar.get("total_chunks", 1) if sidecar else 1
                source = sidecar.get("source", name) if sidecar else name
                doc_meta = (
                    sidecar.get("chunk", {}).get("metadata", {})
                    if sidecar
                    else {}
                )

                summary = summaries.get(resolved_id)
                if summary is None:
                    summaries[resolved_id] = DocumentSummary(
                        document_id=resolved_id,
                        source=source,
                        chunk_count=real_total,
                        metadata=dict(doc_meta),
                    )
                else:
                    # Use the max of total_chunks across all sidecars
                    # for the same document (they should agree).
                    summary.chunk_count = max(
                        summary.chunk_count,
                        real_total,
                    )

            if len(docs) < 100:
                break
            page += 1

        return list(summaries.values())
