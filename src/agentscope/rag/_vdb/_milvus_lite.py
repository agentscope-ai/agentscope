# -*- coding: utf-8 -*-
"""Milvus Lite implementation of the vector store backend.

Built on the official ``pymilvus`` ``MilvusClient`` API. Milvus Lite is
started automatically when the URI points to a local ``.db`` file, so it
is convenient for local development, tests, and small RAG workloads.
"""
import asyncio
import json
import os
import uuid
from typing import TYPE_CHECKING, Any, Literal

from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)
from .._document import Chunk

if TYPE_CHECKING:
    from pymilvus import MilvusClient


class MilvusLiteStore(VectorStoreBase):
    """Vector store backend backed by Milvus Lite.

    Each knowledge base maps to one Milvus collection. Every entity
    stores the owning ``document_id``, the serialized
    :class:`~agentscope.rag.Chunk`, and the chunk metadata in a JSON
    field used for flat equality filtering.

    .. note:: Install optional dependencies with
        ``pip install agentscope[milvuslite]``.

    .. code-block:: python

        store = MilvusLiteStore(uri="./rag_demo.db")

        async with store:
            await store.create_collection("kb_1", dimensions=768)
    """

    def __init__(
        self,
        uri: str = "./agentscope_milvus_lite.db",
        metric_type: Literal["COSINE", "IP", "L2"] = "COSINE",
        index_type: str = "AUTOINDEX",
        client_kwargs: dict[str, Any] | None = None,
        batch_size: int = 256,
    ) -> None:
        """Initialize the Milvus Lite vector store.

        Args:
            uri (`str`, defaults to ``"./agentscope_milvus_lite.db"``):
                Local ``.db`` path for Milvus Lite, or a Milvus-compatible
                endpoint URI.
            metric_type (`Literal["COSINE", "IP", "L2"]`, defaults to \
             ``"COSINE"``):
                The metric used for the vector index and search.
            index_type (`str`, defaults to ``"AUTOINDEX"``):
                The Milvus index type used for newly created collections.
            client_kwargs (`dict[str, Any] | None`, optional):
                Extra keyword arguments forwarded to
                :class:`pymilvus.MilvusClient`.
            batch_size (`int`, defaults to ``256``):
                Batch size used by inserts and document listing.
        """
        self._uri = uri
        self._metric_type = metric_type
        self._index_type = index_type
        self._client_kwargs = client_kwargs or {}
        self._batch_size = batch_size
        self._client: "MilvusClient | None" = None
        self._is_lite_uri = self._is_local_db_uri(uri)

    def get_client(self) -> "MilvusClient":
        """Lazily create and cache the Milvus client."""
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(uri=self._uri, **self._client_kwargs)
        return self._client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context and close the underlying client."""
        if self._client is None:
            return

        client = self._client
        self._client = None
        close = getattr(client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)
        if self._is_lite_uri:
            await asyncio.to_thread(self._release_lite_server)

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    async def create_collection(
        self,
        name: str,
        dimensions: int,
    ) -> None:
        """Create a new Milvus collection.

        No-op if the collection already exists.

        Args:
            name (`str`):
                The collection name. Typically, the knowledge base ID.
            dimensions (`int`):
                The fixed vector dimensionality for this collection.
                All vectors inserted later must have this many elements.
        """
        client = self.get_client()

        def _create() -> None:
            from pymilvus import DataType

            if client.has_collection(collection_name=name):
                client.load_collection(collection_name=name)
                return

            schema = client.create_schema(
                auto_id=False,
                enable_dynamic_field=False,
            )
            schema.add_field(
                field_name="id",
                datatype=DataType.VARCHAR,
                is_primary=True,
                max_length=64,
            )
            schema.add_field(
                field_name="vector",
                datatype=DataType.FLOAT_VECTOR,
                dim=dimensions,
            )
            schema.add_field(
                field_name="document_id",
                datatype=DataType.VARCHAR,
                max_length=512,
            )
            schema.add_field(
                field_name="chunk",
                datatype=DataType.JSON,
            )
            schema.add_field(
                field_name="metadata",
                datatype=DataType.JSON,
            )

            index_params = client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_type=self._index_type,
                metric_type=self._metric_type,
            )
            client.create_collection(
                collection_name=name,
                schema=schema,
                index_params=index_params,
            )
            client.load_collection(collection_name=name)

        await asyncio.to_thread(_create)

    async def delete_collection(self, name: str) -> None:
        """Delete a collection and all its data."""
        await asyncio.to_thread(
            self.get_client().drop_collection,
            collection_name=name,
        )

    async def has_collection(self, name: str) -> bool:
        """Check whether a collection exists."""
        return await asyncio.to_thread(
            self.get_client().has_collection,
            collection_name=name,
        )

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection.

        Each entity stores the :attr:`VectorRecord.document_id`, the
        serialized :class:`Chunk`, and a copy of ``chunk.metadata`` in a
        JSON field used by :meth:`search` and :meth:`list_documents` for
        flat equality filtering.

        Args:
            collection (`str`):
                The target collection name.
            records (`list[VectorRecord]`):
                The records to insert, each carrying a
                :class:`Chunk` and its embedding vector.
        """
        if not records:
            return

        for start in range(0, len(records), self._batch_size):
            batch = records[start : start + self._batch_size]
            await asyncio.to_thread(
                self.get_client().insert,
                collection_name=collection,
                data=[
                    {
                        "id": str(uuid.uuid4()),
                        "vector": record.vector,
                        "document_id": record.document_id,
                        "chunk": record.chunk.model_dump(mode="json"),
                        "metadata": record.chunk.metadata,
                    }
                    for record in batch
                ],
            )

    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document."""
        await asyncio.to_thread(
            self.get_client().delete,
            collection_name=collection,
            filter=self._build_document_filter(document_id),
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
        """Find the most similar records to a query vector.

        Args:
            collection (`str`):
                The collection to search.
            query_vector (`list[float]`):
                The query embedding vector.
            top_k (`int`, defaults to ``5``):
                Maximum number of results to return.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict the search to records whose
                ``chunk.metadata`` matches every ``key == value`` pair
                in this dict. Milvus Lite stores this metadata in a JSON
                field and supports flat scalar equality filters.

        Returns:
            `list[VectorSearchResult]`:
                Results ordered by descending similarity score for
                cosine / inner product metrics, or ascending distance
                semantics for L2 as exposed by Milvus.
        """
        response = await asyncio.to_thread(
            self.get_client().search,
            collection_name=collection,
            data=[query_vector],
            anns_field="vector",
            limit=top_k,
            filter=self._build_metadata_filter(metadata_filter),
            output_fields=["document_id", "chunk"],
            search_params={"metric_type": self._metric_type},
        )

        hits = response[0] if response else []
        return [
            VectorSearchResult(
                score=self._extract_score(hit, self._metric_type),
                document_id=hit["entity"]["document_id"],
                chunk=Chunk.model_validate(hit["entity"]["chunk"]),
            )
            for hit in hits
        ]

    # ------------------------------------------------------------------
    # Document listing
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List all distinct source documents indexed in a collection.

        Scans stored entities and aggregates them by ``document_id``.
        The first chunk encountered for each document supplies the
        source filename and document-level metadata.

        Args:
            collection (`str`):
                The target collection name.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict aggregation to records whose
                ``chunk.metadata`` matches every ``key == value`` pair
                in this dict.

        Returns:
            `list[DocumentSummary]`:
                One summary per distinct ``document_id``.
        """
        client = self.get_client()
        rows = await asyncio.to_thread(
            self._query_all_rows,
            client,
            collection,
            metadata_filter,
        )
        summaries: dict[str, DocumentSummary] = {}
        seen_ids: set[str] = set()

        for row in rows:
            record_id = row.get("id")
            if record_id in seen_ids:
                continue
            if record_id is not None:
                seen_ids.add(record_id)

            doc_id = row["document_id"]
            summary = summaries.get(doc_id)
            if summary is None:
                chunk_payload = row["chunk"]
                summaries[doc_id] = DocumentSummary(
                    document_id=doc_id,
                    source=chunk_payload.get("source", ""),
                    chunk_count=1,
                    metadata=dict(chunk_payload.get("metadata", {})),
                )
            else:
                summary.chunk_count += 1

        return list(summaries.values())

    def _query_all_rows(
        self,
        client: "MilvusClient",
        collection: str,
        metadata_filter: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Query all rows needed by ``list_documents``."""
        output_fields = ["id", "document_id", "chunk"]
        expr = self._build_metadata_filter(metadata_filter)

        if hasattr(client, "query_iterator"):
            iterator = client.query_iterator(
                collection_name=collection,
                filter=expr,
                output_fields=output_fields,
                batch_size=self._batch_size,
            )
            try:
                rows = []
                while True:
                    batch = iterator.next()
                    if not batch:
                        break
                    rows.extend(batch)
                return rows
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()

        rows = []
        offset = 0
        while True:
            batch = client.query(
                collection_name=collection,
                filter=expr,
                output_fields=output_fields,
                limit=self._batch_size,
                offset=offset,
            )
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < self._batch_size:
                break
            offset += self._batch_size
        return rows

    @staticmethod
    def _build_document_filter(document_id: str) -> str:
        """Build a Milvus scalar filter for a document id."""
        return f"document_id == {json.dumps(document_id, ensure_ascii=False)}"

    @staticmethod
    def _build_metadata_filter(
        metadata_filter: dict[str, Any] | None,
    ) -> str:
        """Translate a flat ``{key: value}`` filter into a Milvus expr."""
        if not metadata_filter:
            return ""

        return " and ".join(
            f"metadata[{json.dumps(key, ensure_ascii=False)}] == "
            f"{json.dumps(value, ensure_ascii=False)}"
            for key, value in metadata_filter.items()
        )

    @staticmethod
    def _extract_score(
        hit: dict[str, Any],
        metric_type: Literal["COSINE", "IP", "L2"],
    ) -> float:
        """Extract a score from Milvus search result shapes."""
        if "distance" in hit:
            distance = float(hit["distance"])
            if metric_type == "COSINE":
                return 1.0 - distance
            return distance
        if "score" in hit:
            return float(hit["score"])
        return 0.0

    @staticmethod
    def _is_local_db_uri(uri: str) -> bool:
        """Check whether a URI starts an embedded Milvus Lite server."""
        return not uri.startswith(("http://", "https://")) and (
            os.path.splitext(uri)[1] == ".db"
        )

    def _release_lite_server(self) -> None:
        """Release the embedded Milvus Lite server for local ``.db`` URIs."""
        try:
            from milvus_lite import server_manager_instance
        except ImportError:
            return
        server_manager_instance.release_server(self._uri)
