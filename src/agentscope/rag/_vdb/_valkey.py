# -*- coding: utf-8 -*-
"""Valkey implementation of the vector store backend.

Built on the ``valkey-glide`` async client, using the Valkey Search module
with HNSW indexing for vector similarity search.

The same class supports both standalone and cluster deployments through
the constructor arguments:

- ``host="localhost", port=6379`` — single-node standalone
- ``cluster_mode=True`` — Valkey cluster with auto-discovery

.. note:: Requires a Valkey server compiled with the Search module
    (``valkey-bundle`` or a custom build with ``valkeysearch``).

.. note:: The ``valkey-glide`` package is required. Install it with
    ``pip install "valkey-glide>=2.4.0,<3.0.0"``.
"""

import asyncio
import json
import uuid
from typing import Any, Literal, Self

from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)
from .._document import Chunk

# Valkey Search distance metric mapping
_DISTANCE_METRICS: dict[str, str] = {
    "COSINE": "COSINE",
    "L2": "L2",
    "IP": "IP",
}

# Default HNSW parameters
_DEFAULT_HNSW_M = 16
_DEFAULT_HNSW_EF_CONSTRUCTION = 200
_DEFAULT_HNSW_EF_RUNTIME = 10


class ValkeyStore(VectorStoreBase):
    """Vector store backend backed by `Valkey <https://valkey.io>`_ Search.

    Each knowledge base maps to one Valkey Search index (collection).
    Every record is stored as a hash with fields for the vector,
    document ID, and the serialized :class:`~agentscope.rag.Chunk`.

    The index uses HNSW for approximate nearest-neighbor search with
    configurable distance metrics (Cosine, L2, IP).

    .. note:: The ``valkey-glide`` package is required. Install it
        with ``pip install "valkey-glide>=2.4.0,<3.0.0"``.

    .. code-block:: python

        store = ValkeyStore(host="localhost", port=6379)

        async with store:
            await store.create_collection("kb-1", dimensions=768)

    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        cluster_mode: bool = False,
        use_tls: bool = False,
        credentials: tuple[str, str] | None = None,
        client_name: str = "agentscope_rag_store_client",
        distance: Literal["COSINE", "L2", "IP"] = "COSINE",
        hnsw_m: int = _DEFAULT_HNSW_M,
        hnsw_ef_construction: int = _DEFAULT_HNSW_EF_CONSTRUCTION,
        hnsw_ef_runtime: int = _DEFAULT_HNSW_EF_RUNTIME,
        batch_size: int = 100,
        prefix_separator: str = ":",
    ) -> None:
        """Initialize the Valkey vector store.

        Args:
            host (`str`, defaults to ``"localhost"``):
                The Valkey server hostname.
            port (`int`, defaults to ``6379``):
                The Valkey server port.
            cluster_mode (`bool`, defaults to ``False``):
                Whether to connect in cluster mode.
            use_tls (`bool`, defaults to ``False``):
                Whether to use TLS for the connection.
            credentials (`tuple[str, str] | None`, optional):
                A ``(username, password)`` pair for authentication,
                or ``None`` for unauthenticated connections.
            client_name (`str`, defaults to \
             ``"agentscope_rag_store_client"``):
                The client name set via ``CLIENT SETNAME``, visible in
                ``CLIENT LIST`` and monitoring tools.
            distance (`Literal["COSINE", "L2", "IP"]`, defaults to \
             ``"COSINE"``):
                The distance metric used when creating indexes.
            hnsw_m (`int`, defaults to ``16``):
                HNSW ``M`` parameter — max outgoing edges per node.
            hnsw_ef_construction (`int`, defaults to ``200``):
                HNSW ``EF_CONSTRUCTION`` — size of the dynamic
                candidate list during index construction.
            hnsw_ef_runtime (`int`, defaults to ``10``):
                HNSW ``EF_RUNTIME`` — size of the dynamic candidate
                list during search.
            batch_size (`int`, defaults to ``100``):
                Batch size for paginated operations (delete, list).
            prefix_separator (`str`, defaults to ``":"``):
                Separator between the collection prefix and record ID
                in hash keys.
        """
        self._host = host
        self._port = port
        self._cluster_mode = cluster_mode
        self._use_tls = use_tls
        self._credentials = credentials
        self._client_name = client_name
        self._distance = distance
        self._hnsw_m = hnsw_m
        self._hnsw_ef_construction = hnsw_ef_construction
        self._hnsw_ef_runtime = hnsw_ef_runtime
        self._batch_size = batch_size
        self._prefix_separator = prefix_separator
        self._client: Any = None
        self._index_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Enter the async context — open the Valkey connection."""
        self._client = await self._create_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context — close the underlying client."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    def get_client(self) -> Any:
        """Return the cached Valkey client, raising if not connected.

        Returns:
            The Valkey Glide client instance.

        Raises:
            RuntimeError: If called outside an async context manager.
        """
        if self._client is None:
            raise RuntimeError(
                "ValkeyStore must be used as an async context manager. "
                "Use `async with ValkeyStore(...) as store:`",
            )
        return self._client

    async def _create_client(self) -> Any:
        """Create and return a new Valkey Glide client."""
        from glide import (
            GlideClient,
            GlideClientConfiguration,
            GlideClusterClient,
            GlideClusterClientConfiguration,
            NodeAddress,
            ServerCredentials,
        )

        address = NodeAddress(self._host, self._port)
        server_credentials = None
        if self._credentials is not None:
            server_credentials = ServerCredentials(
                password=self._credentials[1],
                username=self._credentials[0],
            )

        if self._cluster_mode:
            config = GlideClusterClientConfiguration(
                addresses=[address],
                use_tls=self._use_tls,
                credentials=server_credentials,
                client_name=self._client_name,
            )
            return await GlideClusterClient.create(config)

        config = GlideClientConfiguration(
            addresses=[address],
            use_tls=self._use_tls,
            credentials=server_credentials,
            client_name=self._client_name,
        )
        return await GlideClient.create(config)

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    async def create_collection(
        self,
        name: str,
        dimensions: int,
    ) -> None:
        """Create a new Valkey Search index for a collection.

        No-op if the index already exists. Uses double-check locking
        to ensure concurrent calls don't race to create the same index.

        Args:
            name (`str`):
                The collection name. Typically, the knowledge base ID.
            dimensions (`int`):
                The fixed vector dimensionality for this collection.
        """
        # Fast path: check without lock
        if await self.has_collection(name):
            return

        lock = self._get_index_lock(name)
        async with lock:
            # Double-check under lock
            if await self.has_collection(name):
                return
            await self._create_index(name, dimensions)

    async def delete_collection(self, name: str) -> None:
        """Delete a collection index and all its data.

        First drops the Valkey Search index, then scans and deletes
        all hash keys with the collection prefix.  The ``DD`` flag is
        not used because not all Valkey Search versions support it.

        Args:
            name (`str`):
                The collection name to delete.
        """
        from glide import RequestError

        client = self.get_client()

        # Drop the index (data hashes are preserved)
        try:
            await client.custom_command(["FT.DROPINDEX", name])
        except RequestError:
            # Index does not exist — continue to clean up hashes
            pass

        # Scan and delete all hashes with the collection prefix
        prefix = self._key_prefix(name)
        cursor = b"0"
        while True:
            result = await client.custom_command(
                ["SCAN", cursor, "MATCH", f"{prefix}*", "COUNT", "100"],
            )
            cursor = result[0]
            keys = result[1]
            if keys:
                for key in keys:
                    await client.custom_command(["DEL", key])
            if cursor == b"0" or cursor == "0":
                break

        self._index_locks.pop(name, None)

    async def has_collection(self, name: str) -> bool:
        """Check whether a collection index exists.

        Args:
            name (`str`):
                The collection name to check.

        Returns:
            `bool`: ``True`` if the index exists.
        """
        from glide import RequestError

        client = self.get_client()
        try:
            await client.custom_command(["FT.INFO", name])
            return True
        except RequestError:
            return False

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection.

        Each record is stored as a Valkey hash with fields for the
        vector embedding, document ID, serialized chunk, and metadata.
        The hash key uses the format ``{collection}:{uuid}``.

        Args:
            collection (`str`):
                The target collection name.
            records (`list[VectorRecord]`):
                The records to insert (each carrying a
                :class:`Chunk` and its embedding vector).
        """
        if not records:
            return

        client = self.get_client()
        prefix = self._key_prefix(collection)

        for record in records:
            key = f"{prefix}{uuid.uuid4().hex}"
            chunk_json = json.dumps(
                record.chunk.model_dump(mode="json"),
                ensure_ascii=False,
            )
            metadata_json = json.dumps(
                record.chunk.metadata,
                ensure_ascii=False,
            )
            vector_bytes = self._encode_vector(record.vector)

            await client.hset(
                key,
                {
                    "vector": vector_bytes,
                    "document_id": record.document_id,
                    "chunk": chunk_json,
                    "metadata": metadata_json,
                    "source": record.chunk.source,
                },
            )

    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document.

        Uses a Valkey Search query filtered by ``document_id`` tag to
        find matching keys, then deletes them in batches.

        Args:
            collection (`str`):
                The target collection name.
            document_id (`str`):
                The source document ID whose records should be
                removed.
        """
        client = self.get_client()
        escaped_id = self._escape_tag_value(document_id)
        query = f"@document_id:{{{escaped_id}}}"

        while True:
            result = await client.custom_command(
                [
                    "FT.SEARCH",
                    collection,
                    query,
                    "NOCONTENT",
                    "LIMIT",
                    "0",
                    str(self._batch_size),
                ],
            )
            keys = self._parse_nocontent_keys(result)
            if not keys:
                break
            for key in keys:
                await client.custom_command(["DEL", key])
            if len(keys) < self._batch_size:
                break

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

        Uses Valkey Search KNN query with optional pre-filtering via
        metadata tag fields.

        Args:
            collection (`str`):
                The collection to search.
            query_vector (`list[float]`):
                The query embedding vector.
            top_k (`int`, defaults to ``5``):
                Maximum number of results to return.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict the search to records whose
                indexed tag fields match every ``key == value`` pair.
                Typically used with ``document_id`` for cross-tenant
                scoping.

        Returns:
            `list[VectorSearchResult]`:
                Results ordered by descending similarity score.
        """
        client = self.get_client()
        vector_bytes = self._encode_vector(query_vector)

        pre_filter = self._build_metadata_filter(metadata_filter)
        query = f"{pre_filter}=>[KNN {top_k} @vector $BLOB]"

        result = await client.custom_command(
            [
                "FT.SEARCH",
                collection,
                query,
                "PARAMS",
                "2",
                "BLOB",
                vector_bytes,
                "RETURN",
                "4",
                "__vector_score",
                "document_id",
                "chunk",
                "source",
                "LIMIT",
                "0",
                str(top_k),
                "DIALECT",
                "2",
            ],
        )

        return self._parse_search_results(result)

    # ------------------------------------------------------------------
    # Document listing
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List all distinct source documents indexed in a collection.

        Scans hash keys with the collection prefix and aggregates by
        ``document_id``. The first chunk encountered for each document
        supplies the ``source`` filename and the document-level
        ``metadata``.

        When ``metadata_filter`` is provided, uses an FT.SEARCH query
        with tag filters.  Otherwise, uses SCAN on the key prefix for
        a full listing (since Valkey Search does not support ``*`` as
        a match-all query).

        Args:
            collection (`str`):
                The target collection name.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict aggregation to records whose
                indexed tag fields match every ``key == value`` pair.

        Returns:
            `list[DocumentSummary]`:
                One summary per distinct ``document_id``.
        """
        if metadata_filter:
            return await self._list_documents_filtered(
                collection,
                metadata_filter,
            )
        return await self._list_documents_scan(collection)

    async def _list_documents_scan(
        self,
        collection: str,
    ) -> list[DocumentSummary]:
        """List documents by scanning all hash keys with the prefix."""
        client = self.get_client()
        prefix = self._key_prefix(collection)
        summaries: dict[str, DocumentSummary] = {}

        cursor = b"0"
        while True:
            result = await client.custom_command(
                [
                    "SCAN",
                    cursor,
                    "MATCH",
                    f"{prefix}*",
                    "COUNT",
                    str(self._batch_size),
                ],
            )
            cursor = result[0]
            keys = result[1]

            for key in keys:
                fields = await client.hgetall(key)
                if not fields:
                    continue
                doc_id = self._decode_field(fields.get(b"document_id", b""))
                summary = summaries.get(doc_id)
                if summary is None:
                    chunk_raw = self._decode_field(
                        fields.get(b"chunk", b"{}"),
                    )
                    chunk_payload = json.loads(chunk_raw)
                    source = self._decode_field(
                        fields.get(b"source", b""),
                    )
                    summaries[doc_id] = DocumentSummary(
                        document_id=doc_id,
                        source=source,
                        chunk_count=1,
                        metadata=dict(chunk_payload.get("metadata", {})),
                    )
                else:
                    summary.chunk_count += 1

            if cursor == b"0" or cursor == "0":
                break

        return list(summaries.values())

    async def _list_documents_filtered(
        self,
        collection: str,
        metadata_filter: dict[str, Any],
    ) -> list[DocumentSummary]:
        """List documents using an FT.SEARCH tag filter."""
        client = self.get_client()
        pre_filter = self._build_metadata_filter(metadata_filter)
        summaries: dict[str, DocumentSummary] = {}
        offset = 0

        while True:
            result = await client.custom_command(
                [
                    "FT.SEARCH",
                    collection,
                    pre_filter,
                    "RETURN",
                    "3",
                    "document_id",
                    "chunk",
                    "source",
                    "LIMIT",
                    str(offset),
                    str(self._batch_size),
                ],
            )
            records = self._parse_dict_results(result)
            if not records:
                break
            for record in records:
                doc_id = record.get("document_id", "")
                summary = summaries.get(doc_id)
                if summary is None:
                    chunk_payload = json.loads(
                        record.get("chunk", "{}"),
                    )
                    summaries[doc_id] = DocumentSummary(
                        document_id=doc_id,
                        source=record.get("source", ""),
                        chunk_count=1,
                        metadata=dict(chunk_payload.get("metadata", {})),
                    )
                else:
                    summary.chunk_count += 1
            if len(records) < self._batch_size:
                break
            offset += self._batch_size

        return list(summaries.values())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _key_prefix(self, collection: str) -> str:
        """Return the hash key prefix for a collection."""
        return f"{collection}{self._prefix_separator}"

    def _get_index_lock(self, name: str) -> asyncio.Lock:
        """Get or create an asyncio lock for index creation."""
        if name not in self._index_locks:
            self._index_locks[name] = asyncio.Lock()
        return self._index_locks[name]

    async def _create_index(self, name: str, dimensions: int) -> None:
        """Create a Valkey Search index with HNSW vector indexing.

        Only the ``vector``, ``document_id``, and ``source`` fields are
        declared in the schema.  The ``chunk`` and ``metadata`` fields
        are stored in each hash but intentionally left out of the index
        — they are retrieved via ``RETURN`` in FT.SEARCH queries
        without needing to be indexed.
        """
        client = self.get_client()
        prefix = self._key_prefix(name)

        await client.custom_command(
            [
                "FT.CREATE",
                name,
                "ON",
                "HASH",
                "PREFIX",
                "1",
                prefix,
                "SCHEMA",
                "vector",
                "VECTOR",
                "HNSW",
                "10",
                "TYPE",
                "FLOAT32",
                "DIM",
                str(dimensions),
                "DISTANCE_METRIC",
                _DISTANCE_METRICS[self._distance],
                "M",
                str(self._hnsw_m),
                "EF_CONSTRUCTION",
                str(self._hnsw_ef_construction),
                "document_id",
                "TAG",
                "SEPARATOR",
                "|",
                "source",
                "TAG",
                "SEPARATOR",
                "|",
            ],
        )

    @staticmethod
    def _encode_vector(vector: list[float]) -> bytes:
        """Encode a float vector to little-endian bytes for Valkey."""
        import struct

        return struct.pack(f"<{len(vector)}f", *vector)

    @staticmethod
    def _escape_tag_value(value: str) -> str:
        """Escape special characters in a Valkey Search tag value.

        Tag queries require certain characters to be escaped with a
        backslash.
        """
        special_chars = r",.<>{}[]\"':;!@#$%^&*()-+=~/ "
        escaped = []
        for char in value:
            if char in special_chars:
                escaped.append(f"\\{char}")
            else:
                escaped.append(char)
        return "".join(escaped)

    @staticmethod
    def _build_metadata_filter(
        metadata_filter: dict[str, Any] | None,
    ) -> str:
        """Build a Valkey Search pre-filter from metadata key-value pairs.

        Supports filtering on indexed TAG fields (``document_id``,
        ``source``).  Returns ``*`` (match all) when no filter is
        provided.

        Args:
            metadata_filter (`dict[str, Any] | None`):
                The flat filter, or ``None`` for no filter.

        Returns:
            `str`: A Valkey Search query expression.
        """
        if not metadata_filter:
            return "*"

        conditions = []
        for key, value in metadata_filter.items():
            escaped_value = ValkeyStore._escape_tag_value(str(value))
            conditions.append(f"@{key}:{{{escaped_value}}}")

        return " ".join(conditions) if conditions else "*"

    @staticmethod
    def _parse_nocontent_keys(result: Any) -> list[str]:
        """Extract key names from an FT.SEARCH NOCONTENT result.

        Valkey Search returns NOCONTENT results as:
        ``[total_count, {key1: {}, key2: {}, ...}]``
        or ``[total_count, key1, key2, ...]`` depending on version.
        """
        if result is None:
            return []

        if isinstance(result, list) and len(result) >= 1:
            total = result[0]
            if isinstance(total, bytes):
                total = int(total)
            if total == 0:
                return []

            if len(result) >= 2 and isinstance(result[1], dict):
                # Dict-style response: [count, {key: {}, ...}]
                return [
                    k.decode("utf-8") if isinstance(k, bytes) else k
                    for k in result[1].keys()
                ]
            # Legacy list-style: [count, key1, key2, ...]
            keys = []
            for item in result[1:]:
                if isinstance(item, bytes):
                    keys.append(item.decode("utf-8"))
                elif isinstance(item, str):
                    keys.append(item)
            return keys

        return []

    @staticmethod
    def _parse_dict_results(result: Any) -> list[dict[str, str]]:
        """Parse FT.SEARCH result in Valkey dict format.

        Valkey Search returns results as:
        ``[total, {key1: {field1: val1, ...}, key2: {...}}]``

        Returns a list of field dicts (one per result), with all
        keys and values decoded to str.
        """
        if result is None:
            return []

        if not isinstance(result, list) or len(result) < 2:
            return []

        total = result[0]
        if isinstance(total, bytes):
            total = int(total)
        if total == 0:
            return []

        data = result[1]
        if not isinstance(data, dict):
            return []

        records = []
        for _key, fields in data.items():
            if not isinstance(fields, dict):
                continue
            record: dict[str, str] = {}
            for field_name, field_value in fields.items():
                name = (
                    field_name.decode("utf-8")
                    if isinstance(field_name, bytes)
                    else field_name
                )
                value = (
                    field_value.decode("utf-8")
                    if isinstance(field_value, bytes)
                    else str(field_value)
                )
                record[name] = value
            records.append(record)
        return records

    def _parse_search_results(
        self,
        result: Any,
    ) -> list[VectorSearchResult]:
        """Parse FT.SEARCH KNN results into VectorSearchResult objects.

        Valkey Search returns KNN results sorted by ascending distance
        with a ``__vector_score`` field.  We convert to a similarity
        score (higher = more similar).
        """
        records = self._parse_dict_results(result)
        results = []
        for record in records:
            raw_score = float(record.get("__vector_score", "0"))
            score = self._to_similarity_score(raw_score)
            chunk_json = record.get("chunk", "{}")
            chunk = Chunk.model_validate(json.loads(chunk_json))
            results.append(
                VectorSearchResult(
                    score=score,
                    document_id=record.get("document_id", ""),
                    chunk=chunk,
                ),
            )
        return results

    @staticmethod
    def _decode_field(value: Any) -> str:
        """Decode a bytes or str field value to str."""
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value) if value is not None else ""

    def _to_similarity_score(self, distance: float) -> float:
        """Convert a Valkey Search distance to a similarity score.

        For COSINE: Valkey returns ``1 - cosine_similarity``, so
            similarity = ``1 - distance``.
        For IP (inner product): Valkey returns ``1 - IP``, so
            similarity = ``1 - distance``.
        For L2: Valkey returns the squared L2 distance directly;
            lower distance = more similar. We return the negative
            so that higher = more similar, consistent with the
            interface contract.
        """
        if self._distance in ("COSINE", "IP"):
            return 1.0 - distance
        # L2: return negative distance so higher = more similar
        return -distance
