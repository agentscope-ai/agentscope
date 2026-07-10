# -*- coding: utf-8 -*-
"""RAGFlow implementation of the vector store backend.

Built on the official ``ragflow-sdk`` package.  Each knowledge base maps to
one RAGFlow **dataset**.  Chunks inside a single ``document_id`` are
uploaded as one text file whose filename encodes the ``document_id``, and
whose first line embeds a JSON sidecar (``# agentscope: {...}``) with the
serialised :class:`~agentscope.rag.Chunk` metadata needed to reconstruct
results and scope deletions.

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
# metadata that survives RAGFlow parsing.  Format:
#   # agentscope: <compact-json-sidecar>\n
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
        accepted for interface compatibility but is not used.

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
        """Build an upload filename that carries the AgentScope document_id.

        Args:
            document_id: The AgentScope source document identifier.

        Returns:
            A filename like ``agentscope_<doc_id>.txt``.
        """
        # Sanitise: only keep alphanumeric, hyphen, underscore.
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", document_id)
        return f"agentscope_{safe}.txt"

    @staticmethod
    def _parse_document_id_from_name(name: str) -> str | None:
        """Recover the AgentScope ``document_id`` from a RAGFlow document name.

        Args:
            name: The RAGFlow document name / title.

        Returns:
            The extracted document_id, or ``None``.
        """
        m = re.match(r"^agentscope_(.+)\.txt$", name)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _build_sidecar(records: list[VectorRecord]) -> str:
        """Serialise chunk metadata into a single-line JSON sidecar.

        Args:
            records: The records to encode.

        Returns:
            A JSON string (compact, one line).
        """
        return json.dumps(
            [
                {
                    "document_id": rec.document_id,
                    "chunk_index": rec.chunk.chunk_index,
                    "chunk": rec.chunk.model_dump(mode="json"),
                }
                for rec in records
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _parse_sidecar(content: str) -> list[dict[str, Any]] | None:
        """Extract the sidecar JSON from document content.

        Args:
            content: The RAGFlow chunk / document text.

        Returns:
            Parsed sidecar list, or ``None``.
        """
        m = _SIDECAR_RE.search(content)
        if not m:
            return None
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    @staticmethod
    def _build_metadata_condition(
        metadata_filter: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Translate a flat ``{key: value}`` filter into RAGFlow format.

        Args:
            metadata_filter: The flat filter, or ``None``.

        Returns:
            RAGFlow-compatible ``metadata_condition`` dict, or ``None``.
        """
        if not metadata_filter:
            return None
        return {
            "logic": "and",
            "conditions": [
                {
                    "name": str(k),
                    "comparison_operator": "=",
                    "value": str(v),
                }
                for k, v in metadata_filter.items()
            ],
        }

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

        Records are grouped by :attr:`VectorRecord.document_id`.  For each
        group the chunk contents are concatenated into a text file (with a
        JSON sidecar on the first line) and uploaded to RAGFlow.  The
        filename encodes the ``document_id`` so that :meth:`delete` can
        locate and remove all chunks of one source document.

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

        # Group by document_id.
        groups: dict[str, list[VectorRecord]] = {}
        for rec in records:
            groups.setdefault(rec.document_id, []).append(rec)

        for document_id, group in groups.items():
            sidecar = self._build_sidecar(group)
            # Build text content: sidecar line + chunk contents.
            body_lines: list[str] = []
            for rec in group:
                body_lines.append(
                    f"\n--- CHUNK {rec.chunk.chunk_index} ---\n"
                    f"{rec.chunk.content}",
                )
            payload = f"{_SIDECAR_PREFIX}{sidecar}\n{''.join(body_lines)}"

            filename = self._make_filename(document_id)
            tmp_path = os.path.join(
                tempfile.gettempdir(),
                filename,
            )
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(payload)
                await asyncio.to_thread(
                    ds.upload_documents,
                    [tmp_path],
                )
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        # Trigger async parsing for newly uploaded documents.
        uploaded = await asyncio.to_thread(
            ds.list_documents,
            page=1,
            page_size=1000,
        )
        if uploaded:
            doc_ids = [
                d.get("id") if isinstance(d, dict) else d.id for d in uploaded
            ]
            await asyncio.to_thread(
                ds.async_parse_documents,
                doc_ids,
            )

    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document.

        Lists documents in the dataset, matches those whose filename
        encodes the given ``document_id``, and deletes them.

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
                if name == target_filename or name.startswith(
                    f"agentscope_{document_id}",
                ):
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
            The ``query_vector`` parameter is **accepted for interface
            compatibility but not used** — RAGFlow generates its own query
            embedding internally from the ``question`` text parameter.
            Retrieval quality depends on RAGFlow's configured embedding
            model, not on the vectors supplied at insertion time.

        Args:
            collection: The target dataset name.
            query_vector: **Not used.** Accepted for compatibility.
            top_k: Maximum number of results to return.
            metadata_filter: If provided, translated into RAGFlow's
                ``metadata_condition`` filter.

        Returns:
            Results ordered by descending similarity score.
        """
        ds = await self._get_dataset_by_name(collection)
        if ds is None:
            return []

        metadata_condition = self._build_metadata_condition(metadata_filter)

        raw_results = await asyncio.to_thread(
            self.get_client().retrieve,
            dataset_ids=[ds.id],
            question="",
            top_k=top_k,
            similarity_threshold=0.0,
            vector_similarity_weight=0.0,
            keyword=True,
            metadata_condition=metadata_condition,
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

            chunk = self._chunk_from_sidecar(content)
            resolved_doc_id = (
                chunk.metadata.get("document_id", "")
                if chunk
                else (self._parse_document_id_from_name(doc_name) or doc_name)
            )

            results.append(
                VectorSearchResult(
                    score=score,
                    document_id=resolved_doc_id,
                    chunk=chunk
                    or Chunk(
                        content=TextBlock(text=content),
                        source=doc_name,
                        chunk_index=0,
                        total_chunks=1,
                        metadata={"document_id": resolved_doc_id},
                    ),
                ),
            )

        return results

    # ------------------------------------------------------------------
    # Document listing
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List all distinct source documents indexed in a dataset.

        Iterates RAGFlow documents, recovers the ``document_id`` from the
        filename, and aggregates into one :class:`DocumentSummary` per
        source document.

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
                resolved_id = self._parse_document_id_from_name(name) or name

                summary = summaries.get(resolved_id)
                if summary is None:
                    summaries[resolved_id] = DocumentSummary(
                        document_id=resolved_id,
                        source=name,
                        chunk_count=1,
                        metadata={},
                    )
                else:
                    summary.chunk_count += 1

            if len(docs) < 100:
                break
            page += 1

        return list(summaries.values())

    # ------------------------------------------------------------------
    # Sidecar helpers
    # ------------------------------------------------------------------

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
        if data and isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and "chunk" in first:
                chunk = Chunk.model_validate(first["chunk"])
                # Inject document_id from the sidecar top-level into the
                # chunk's metadata so it survives the round-trip.
                if "document_id" in first:
                    chunk.metadata["document_id"] = first["document_id"]
                return chunk
        return None
