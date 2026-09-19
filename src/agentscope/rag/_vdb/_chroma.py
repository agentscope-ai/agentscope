# -*- coding: utf-8 -*-
"""Chroma implementation of the vector store backend."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal

from .._document import Chunk
from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)

if TYPE_CHECKING:
    from chromadb.api import ClientAPI


_DOCUMENT_ID_KEY = "_agentscope_document_id"
_CHUNK_INDEX_KEY = "_agentscope_chunk_index"
_DIMENSIONS_KEY = "_agentscope_dimensions"
_METADATA_PREFIX = "_agentscope_metadata_"


class ChromaStore(VectorStoreBase):
    """Vector store backend backed by `Chroma <https://www.trychroma.com>`_.

    Chroma collections store the complete serialized :class:`Chunk` in the
    document field.  Internal metadata fields provide stable document and
    chunk identifiers, while user metadata is JSON-encoded into separate
    fields so equality filters work for scalar and structured values alike.

    The Chroma Python client is synchronous, so every database operation is
    executed in a worker thread to keep AgentScope's async event loop
    responsive.  A local persistent client, an in-memory client, and a remote
    HTTP client are supported.

    .. note:: Requires the optional ``chromadb`` package. Install it with
        ``pip install agentscope[vdb-chroma]``.

    .. code-block:: python

        store = ChromaStore(path="./agentscope_chroma")

        async with store:
            await store.create_collection("kb-1", dimensions=768)

        # For a Chroma server, use ``host`` instead of ``path``.
        store = ChromaStore(host="localhost", port=8000)
    """

    def __init__(
        self,
        path: str | None = None,
        *,
        host: str | None = None,
        port: int = 8000,
        ssl: bool = False,
        headers: dict[str, str] | None = None,
        tenant: str = "default_tenant",
        database: str = "default_database",
        distance: Literal["cosine", "l2", "ip"] = "cosine",
        batch_size: int = 256,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Chroma vector store.

        Args:
            path (`str | None`, optional):
                Directory for a local persistent Chroma client. If both
                ``path`` and ``host`` are ``None``, an in-memory client is
                used.
            host (`str | None`, optional):
                Hostname of a remote Chroma HTTP server. Mutually exclusive
                with ``path``.
            port (`int`, defaults to ``8000``):
                HTTP port of the remote Chroma server.
            ssl (`bool`, defaults to ``False``):
                Whether the remote HTTP connection uses TLS.
            headers (`dict[str, str] | None`, optional):
                Additional headers sent to the remote Chroma server.
            tenant (`str`, defaults to ``"default_tenant"``):
                Chroma tenant to use.
            database (`str`, defaults to ``"default_database"``):
                Chroma database to use.
            distance (`Literal["cosine", "l2", "ip"]`, defaults to
             ``"cosine"``):
                Distance metric used by new collections. Chroma returns
                distances, which this store normalizes to higher-is-better
                scores.
            batch_size (`int`, defaults to ``256``):
                Maximum number of records sent in one Chroma operation.
            client_kwargs (`dict[str, Any] | None`, optional):
                Extra keyword arguments forwarded to the selected Chroma
                client constructor, such as ``settings``.
        """
        if path is not None and host is not None:
            raise ValueError("path and host are mutually exclusive")
        if not 1 <= port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        if distance not in ("cosine", "l2", "ip"):
            raise ValueError("distance must be one of: cosine, l2, ip")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        self._path = path
        self._host = host
        self._port = port
        self._ssl = ssl
        self._headers = headers
        self._tenant = tenant
        self._database = database
        self._distance = distance
        self._batch_size = batch_size
        self._client_kwargs = dict(client_kwargs or {})
        self._client: "ClientAPI | None" = None

    def get_client(self) -> "ClientAPI":
        """Lazily create and cache the configured Chroma client."""
        if self._client is None:
            import chromadb

            kwargs = dict(self._client_kwargs)
            kwargs.setdefault("tenant", self._tenant)
            kwargs.setdefault("database", self._database)

            if self._host is not None:
                kwargs.setdefault("host", self._host)
                kwargs.setdefault("port", self._port)
                kwargs.setdefault("ssl", self._ssl)
                if self._headers is not None:
                    kwargs.setdefault("headers", self._headers)
                self._client = chromadb.HttpClient(**kwargs)
            elif self._path is not None:
                self._client = chromadb.PersistentClient(
                    path=self._path,
                    **kwargs,
                )
            else:
                self._client = chromadb.EphemeralClient(**kwargs)
        return self._client

    async def _get_client(self) -> "ClientAPI":
        """Get the client without constructing it on the event-loop thread."""
        return await asyncio.to_thread(self.get_client)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Close the Chroma client when the SDK exposes a close method."""
        del exc_type, exc_val, exc_tb
        if self._client is None:
            return

        close = getattr(self._client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)
        self._client = None

    async def _get_collection(self, name: str) -> Any:
        """Get a collection without invoking the synchronous SDK directly."""
        client = await self._get_client()
        return await asyncio.to_thread(
            client.get_collection,
            name=name,
            embedding_function=None,
        )

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    async def create_collection(self, name: str, dimensions: int) -> None:
        """Create a Chroma collection if it does not already exist.

        Chroma infers a collection's vector dimensionality from its first
        insert, so the declared dimension is retained in collection metadata
        and checked when the collection is reused.
        """
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")

        client = await self._get_client()
        metadata = {
            "hnsw:space": self._distance,
            _DIMENSIONS_KEY: dimensions,
        }
        collection = await asyncio.to_thread(
            client.get_or_create_collection,
            name=name,
            metadata=metadata,
            embedding_function=None,
        )
        self._validate_dimensions(collection, dimensions)

    async def delete_collection(self, name: str) -> None:
        """Delete a collection and all its records."""
        client = await self._get_client()
        await asyncio.to_thread(client.delete_collection, name=name)

    async def has_collection(self, name: str) -> bool:
        """Return whether a collection exists in the configured database."""
        client = await self._get_client()
        collections = await asyncio.to_thread(client.list_collections)
        return any(
            getattr(collection, "name", collection) == name
            for collection in collections
        )

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Upsert records with deterministic IDs derived from chunks."""
        if not records:
            return

        chroma_collection = await self._get_collection(collection)
        for start in range(0, len(records), self._batch_size):
            batch = records[start : start + self._batch_size]
            await asyncio.to_thread(
                chroma_collection.upsert,
                ids=[self._record_id(record) for record in batch],
                embeddings=[record.vector for record in batch],
                documents=[
                    self._serialize_chunk(record.chunk) for record in batch
                ],
                metadatas=[self._record_metadata(record) for record in batch],
            )

    async def delete(self, collection: str, document_id: str) -> None:
        """Delete all records belonging to one source document."""
        chroma_collection = await self._get_collection(collection)
        await asyncio.to_thread(
            chroma_collection.delete,
            where={_DOCUMENT_ID_KEY: document_id},
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
        """Find the most similar records to a query vector."""
        if top_k <= 0:
            return []

        chroma_collection = await self._get_collection(collection)
        response = await asyncio.to_thread(
            chroma_collection.query,
            query_embeddings=[query_vector],
            n_results=top_k,
            where=self._build_metadata_filter(metadata_filter),
            include=["documents", "metadatas", "distances"],
        )
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]

        return [
            VectorSearchResult(
                score=self._normalize_score(float(distance)),
                document_id=metadata[_DOCUMENT_ID_KEY],
                chunk=self._deserialize_chunk(document),
            )
            for document, metadata, distance in zip(
                documents,
                metadatas,
                distances,
            )
        ]

    # ------------------------------------------------------------------
    # Document listing
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List distinct source documents by scanning paged Chroma records."""
        chroma_collection = await self._get_collection(collection)
        summaries: dict[str, DocumentSummary] = {}
        offset = 0

        while True:
            response = await asyncio.to_thread(
                chroma_collection.get,
                where=self._build_metadata_filter(metadata_filter),
                limit=self._batch_size,
                offset=offset,
                include=["documents", "metadatas"],
            )
            documents = response.get("documents") or []
            metadatas = response.get("metadatas") or []
            if not documents:
                break

            for document, metadata in zip(documents, metadatas):
                chunk = self._deserialize_chunk(document)
                document_id = metadata[_DOCUMENT_ID_KEY]
                summary = summaries.get(document_id)
                if summary is None:
                    summaries[document_id] = DocumentSummary(
                        document_id=document_id,
                        source=chunk.source,
                        chunk_count=1,
                        metadata=dict(chunk.metadata),
                    )
                else:
                    summary.chunk_count += 1

            if len(documents) < self._batch_size:
                break
            offset += len(documents)

        return list(summaries.values())

    # ------------------------------------------------------------------
    # Chunk listing
    # ------------------------------------------------------------------

    async def list_chunks(
        self,
        collection: str,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """List one document's chunks in ascending ``chunk_index`` order."""
        if limit <= 0:
            return []

        conditions: list[dict[str, Any]] = [
            {_DOCUMENT_ID_KEY: document_id},
            {_CHUNK_INDEX_KEY: {"$gte": offset}},
            {_CHUNK_INDEX_KEY: {"$lt": offset + limit}},
        ]
        conditions.extend(self._metadata_conditions(metadata_filter))
        where: dict[str, Any] = {"$and": conditions}

        chroma_collection = await self._get_collection(collection)
        chunks: dict[int, Chunk] = {}
        page_offset = 0
        while True:
            response = await asyncio.to_thread(
                chroma_collection.get,
                where=where,
                limit=self._batch_size,
                offset=page_offset,
                include=["documents"],
            )
            documents = response.get("documents") or []
            if not documents:
                break

            for document in documents:
                chunk = self._deserialize_chunk(document)
                chunks.setdefault(chunk.chunk_index, chunk)

            if len(documents) < self._batch_size:
                break
            page_offset += len(documents)

        return [chunks[index] for index in sorted(chunks)][:limit]

    # ------------------------------------------------------------------
    # Serialization and filter helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _record_id(record: VectorRecord) -> str:
        """Build a stable ID so retrying an insert replaces the same chunk."""
        raw = f"{record.document_id}\0{record.chunk.chunk_index}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _serialize_chunk(chunk: Chunk) -> str:
        """Serialize a chunk as a JSON document accepted by Chroma."""
        return json.dumps(
            chunk.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _deserialize_chunk(document: str) -> Chunk:
        """Restore a Chunk from the JSON document stored in Chroma."""
        return Chunk.model_validate(json.loads(document))

    @classmethod
    def _record_metadata(cls, record: VectorRecord) -> dict[str, Any]:
        """Build Chroma metadata for internal IDs and user filtering."""
        metadata: dict[str, Any] = {
            _DOCUMENT_ID_KEY: record.document_id,
            _CHUNK_INDEX_KEY: record.chunk.chunk_index,
        }
        metadata.update(
            {
                f"{_METADATA_PREFIX}{key}": cls._encode_metadata_value(value)
                for key, value in record.chunk.metadata.items()
            },
        )
        return metadata

    @classmethod
    def _metadata_conditions(
        cls,
        metadata_filter: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Translate metadata equality filters into Chroma predicates."""
        return [
            {
                f"{_METADATA_PREFIX}{key}": cls._encode_metadata_value(value),
            }
            for key, value in (metadata_filter or {}).items()
        ]

    @classmethod
    def _build_metadata_filter(
        cls,
        metadata_filter: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Build a Chroma ``where`` expression for flat equality filters."""
        conditions = cls._metadata_conditions(metadata_filter)
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _encode_metadata_value(value: Any) -> str:
        """Encode any JSON-compatible metadata value as a Chroma string."""
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _normalize_score(self, distance: float) -> float:
        """Convert Chroma's lower-is-better distance into a score."""
        if self._distance in ("cosine", "ip"):
            return 1.0 - distance
        return -distance

    @staticmethod
    def _validate_dimensions(collection: Any, dimensions: int) -> None:
        """Validate a dimension declaration stored on a Chroma collection."""
        metadata = collection.metadata or {}
        stored_dimensions = metadata.get(_DIMENSIONS_KEY)
        if (
            stored_dimensions is not None
            and int(stored_dimensions) != dimensions
        ):
            raise ValueError(
                "Chroma collection dimension mismatch: existing collection "
                f"uses {stored_dimensions}, requested {dimensions}",
            )


__all__ = ["ChromaStore"]
