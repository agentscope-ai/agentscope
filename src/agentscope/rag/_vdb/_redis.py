# -*- coding: utf-8 -*-
"""Redis implementation of the vector store backend.

Built on ``redis-py``'s native :mod:`redis.commands.search` module (NOT
the ``redisvl`` library — redisvl requires ``redis>=5.0,<8.0``, which
conflicts with the project's pinned redis 8.0.1).  All operations use
the fully asynchronous client (:class:`redis.asyncio.Redis`) with
``FT.CREATE`` / ``FT.SEARCH`` / ``FT.AGGREGATE`` / ``FT.DROPINDEX``
commands.

**Requirements**

- **Redis Stack 7.2** or later, or **Redis 8.0** or later (RediSearch is
  bundled and loaded automatically).
- All vector-search queries use **DIALECT 2** (the minimum dialect that
  supports KNN search and ``PARAMS``).
- One search index per knowledge base (FT index names are global per
  Redis server — use unique collection names).

Install with::

    pip install agentscope[vdb-redis]
"""

import base64
import hashlib
import json
import uuid
from typing import Any, TYPE_CHECKING

import numpy as np

from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)
from .._document import Chunk

if TYPE_CHECKING:
    from redis.asyncio import Redis


# ------------------------------------------------------------------
# Module-level constants
# ------------------------------------------------------------------

# Default HNSW hyper-parameters.  DISTANCE_METRIC must stay COSINE so
# the ``1 - vector_distance`` score conversion yields raw cosine
# similarity (matching the "higher = more similar" contract in
# :class:`VectorSearchResult`).
_HNSW_PARAMS: dict[str, object] = {
    "TYPE": "FLOAT32",
    "DISTANCE_METRIC": "COSINE",
    "M": 16,
    "EF_CONSTRUCTION": 200,
    "EF_RUNTIME": 10,
}

# Maximum number of keys deleted in a single FT.SEARCH round-trip during
# document deletion.
_PAGE_SIZE = 1000

# Number of document summaries fetched per FT.AGGREGATE cursor read.
_AGGREGATE_PAGE_SIZE = 1000

# ------------------------------------------------------------------
# Connection / lifecycle
# ------------------------------------------------------------------


class RedisStore(VectorStoreBase):
    """Vector store backend backed by `Redis Stack
    <https://redis.io/docs/latest/develop/get-started/vector-database/>`_.

    Each knowledge base maps to one RediSearch index whose documents are
    Redis hashes under the ``{collection}:doc:`` key prefix.  Every hash
    stores the serialised :class:`~agentscope.rag.Chunk` plus the owning
    ``document_id``, which is used by :meth:`delete` for whole-document
    removal.

    .. note:: The ``redis`` package is required.  Install it with
        ``pip install agentscope[vdb-redis]``.

    .. code-block:: python

        store = RedisStore(url="redis://localhost:6379")

        async with store:
            await store.create_collection("kb-1", dimensions=768)

    """

    def __init__(
        self,
        url: str = "redis://localhost:6379",
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the Redis vector store.

        No I/O is performed in the constructor — the connection is
        established lazily on first use via :meth:`get_client`.

        Args:
            url (`str`, defaults to ``"redis://localhost:6379"``):
                The Redis connection URL (``redis://``, ``rediss://``,
                or ``unix://``).  Passed directly to
                :func:`redis.asyncio.from_url`.
            client_kwargs (`dict[str, Any] | None`, optional):
                Extra keyword arguments forwarded to
                :func:`redis.asyncio.from_url` (e.g. ``username``,
                ``password``, ``ssl=True``).
        """
        self._url = url
        self._client_kwargs = client_kwargs or {}
        self._client: "Redis | None" = None
        # Per-collection set of metadata keys whose TAG fields have
        # already been added to the index (lazy FT.ALTER).
        self._metadata_fields: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def get_client(self) -> "Redis":
        """Lazily create and cache the async Redis client.

        Returns:
            `redis.asyncio.Redis`:
                The shared async client instance (``decode_responses=True``).
        """
        if self._client is None:
            from redis.asyncio import from_url

            self._client = from_url(  # type: ignore[no-untyped-call]
                self._url,
                decode_responses=True,
                **self._client_kwargs,
            )
        return self._client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context — close the underlying client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    async def create_collection(
        self,
        name: str,
        dimensions: int,
    ) -> None:
        """Create a new RediSearch index (collection).

        No-op if the index already exists.

        Args:
            name (`str`):
                The collection (index) name.  Typically the knowledge
                base ID.  FT index names are global per server — use
                unique names.
            dimensions (`int`):
                The fixed vector dimensionality for this collection.
        """
        from redis.commands.search.field import (
            TagField,
            TextField,
            VectorField,
        )
        from redis.commands.search.index_definition import (
            IndexDefinition,
            IndexType,
        )

        client = self.get_client()
        if await self._has_collection_locked(name):
            return

        schema = (
            TextField("content"),
            TagField(  # type: ignore[no-untyped-call]
                "document_id",
                # URL-safe Base64 values cannot contain this separator.
                separator="\x1f",
            ),
            VectorField(  # type: ignore[no-untyped-call]
                "embedding",
                "HNSW",
                {  # type: ignore[arg-type]
                    "TYPE": "FLOAT32",
                    "DIM": dimensions,
                    **_HNSW_PARAMS,
                },
            ),
        )

        await client.ft(name).create_index(  # type: ignore[no-untyped-call]
            schema,
            definition=IndexDefinition(  # type: ignore[no-untyped-call]
                prefix=[f"{name}:doc:"],
                index_type=IndexType.HASH,
            ),
        )

        # Reset the per-collection metadata-field cache so a recreated
        # index starts with a clean slate.
        self._metadata_fields[name] = set()

    async def delete_collection(self, name: str) -> None:
        """Delete a collection (index) and all its data.

        Tolerates a missing index (no-op), matching
        :class:`MongoDBStore`'s lenient ``drop``.

        Args:
            name (`str`):
                The collection (index) name to delete.
        """
        try:
            # delete_documents=True is required — otherwise the hashes
            # under {name}:doc:* would leak as orphan keys.
            ft = self.get_client().ft(name)
            await ft.dropindex(  # type: ignore[no-untyped-call]
                delete_documents=True,
            )
        except self._response_error() as exc:
            if "unknown index name" not in str(exc).lower():
                raise
        self._metadata_fields.pop(name, None)

    async def has_collection(self, name: str) -> bool:
        """Check whether a collection (index) exists.

        Args:
            name (`str`):
                The collection name to check.

        Returns:
            `bool`: ``True`` if the index exists.
        """
        return await self._has_collection_locked(name)

    async def _has_collection_locked(self, name: str) -> bool:
        """Internal helper — same logic as :meth:`has_collection` but
        without the extra call layer.
        """
        try:
            ft = self.get_client().ft(name)
            await ft.info()  # type: ignore[no-untyped-call]
            return True
        except self._response_error() as exc:
            if "unknown index name" in str(exc).lower():
                return False
            raise

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection.

        Each hash stores the serialised :class:`Chunk` (``content``),
        the owning ``document_id``, the ``embedding`` as float32 bytes,
        and one ``md_<key>`` TAG field per metadata entry (registered
        lazily via ``FT.ALTER`` when a new key is encountered).

        Args:
            collection (`str`):
                The target collection (index) name.
            records (`list[VectorRecord]`):
                The records to insert (each carrying a
                :class:`Chunk` and its embedding vector).
        """
        if not records:
            return

        # Ensure every metadata key has a corresponding TAG field in
        # the index schema (lazy FT.ALTER, cached per collection).
        metadata_keys: set[str] = set()
        for record in records:
            metadata_keys.update(record.chunk.metadata.keys())
        await self._ensure_metadata_fields(collection, metadata_keys)

        client = self.get_client()
        async with client.pipeline(transaction=False) as pipe:
            for record in records:
                mapping: dict[str, Any] = {
                    "content": json.dumps(
                        record.chunk.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    "document_id": self._encode_tag_value(
                        record.document_id,
                    ),
                    "embedding": np.asarray(
                        record.vector,
                        dtype=np.float32,
                    ).tobytes(),
                }
                for key, value in record.chunk.metadata.items():
                    mapping[
                        self._metadata_field_name(key)
                    ] = self._encode_tag_value(
                        self._serialize_metadata_value(value),
                    )
                pipe.hset(  # type: ignore[no-untyped-call]
                    f"{collection}:doc:{uuid.uuid4().hex}",
                    mapping=mapping,
                )
            await pipe.execute()  # type: ignore[no-untyped-call]

    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document.

        Matches the ``document_id`` hash field written by
        :meth:`insert`.

        Uses a paginated search-then-unlink loop because RediSearch has
        no in-query bulk delete.

        Args:
            collection (`str`):
                The target collection (index) name.
            document_id (`str`):
                The source document ID whose records should be removed.
        """
        from redis.commands.search.query import Query

        client = self.get_client()
        encoded_document_id = self._encode_tag_value(document_id)
        while True:
            q = (
                Query(  # type: ignore[no-untyped-call]
                    f"@document_id:{{{encoded_document_id}}}",
                )
                .no_content()
                .paging(0, _PAGE_SIZE)
                .dialect(2)
            )
            ft = client.ft(collection)
            res = await ft.search(q)  # type: ignore[no-untyped-call]
            keys = [doc.id for doc in res.docs]
            if keys:
                await client.unlink(*keys)
            if len(res.docs) < _PAGE_SIZE:
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

        Args:
            collection (`str`):
                The collection (index) to search.
            query_vector (`list[float]`):
                The query embedding vector.
            top_k (`int`, defaults to ``5``):
                Maximum number of results to return.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict the search to records whose
                ``chunk.metadata`` matches every ``key == value`` pair
                in this dict.  Each key is mapped to a ``md_<key>`` TAG
                field clause.

        Returns:
            `list[VectorSearchResult]`:
                Results ordered by descending similarity score.
        """
        if top_k <= 0:
            return []

        from redis.commands.search.query import Query

        filter_prefix = self._build_metadata_filter(metadata_filter)
        query_str = (
            f"{filter_prefix or '*'}=>[KNN {top_k} @embedding $vec AS vd]"
        )
        q = (
            Query(query_str)  # type: ignore[no-untyped-call]
            .sort_by("vd")
            .return_fields("content", "document_id", "vd")
            .paging(0, top_k)
            .dialect(2)
        )

        vec_bytes = np.asarray(query_vector, dtype=np.float32).tobytes()
        res = (
            await self.get_client()
            .ft(collection)
            .search(  # type: ignore[no-untyped-call]
                q,
                {"vec": vec_bytes},
            )
        )

        results: list[VectorSearchResult] = []
        for doc in res.docs:
            raw = doc.content
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            document_id = doc.document_id
            if isinstance(document_id, bytes):
                document_id = document_id.decode("utf-8")
            # COSINE distance in RediSearch is 1 - cos_sim, so
            # converting back yields the raw cosine similarity
            # (identical → 1.0, orthogonal → 0.0).
            score = 1.0 - float(doc.vd)
            results.append(
                VectorSearchResult(
                    score=score,
                    document_id=self._decode_tag_value(document_id),
                    chunk=Chunk.model_validate(json.loads(raw)),
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
        """List all distinct source documents indexed in a collection.

        Uses a cursor-backed ``FT.AGGREGATE`` to group by ``document_id``.
        The first chunk encountered supplies the ``source`` filename and
        document-level ``metadata`` (matching the semantics of the other
        backends).

        Args:
            collection (`str`):
                The target collection (index) name.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict aggregation to records whose
                ``chunk.metadata`` matches every ``key == value`` pair.

        Returns:
            `list[DocumentSummary]`:
                One summary per distinct ``document_id``, in
                unspecified order.
        """
        from redis.commands.search import reducers
        from redis.commands.search.aggregation import (
            AggregateRequest,
            Cursor,
        )

        agg = (
            AggregateRequest(  # type: ignore[no-untyped-call]
                self._build_metadata_filter(metadata_filter) or "*",
            )
            .group_by(
                "@document_id",
                reducers.count().alias(  # type: ignore[no-untyped-call]
                    "chunk_count",
                ),
                reducers.first_value(  # type: ignore[no-untyped-call]
                    "@content",
                ).alias("sample_content"),
            )
            .dialect(2)
            .cursor(count=_AGGREGATE_PAGE_SIZE)
        )

        ft = self.get_client().ft(collection)
        res = await ft.aggregate(agg)  # type: ignore[no-untyped-call]

        summaries: list[DocumentSummary] = []
        while True:
            for row in res.rows:
                # Cursor mode returns flat lists of [field, value, ...]
                # instead of dicts.
                if isinstance(row, list):
                    row = dict(zip(row[::2], row[1::2]))
                document_id = row["document_id"]
                if isinstance(document_id, bytes):
                    document_id = document_id.decode("utf-8")
                chunk_count = int(row["chunk_count"])
                raw_sample = row.get("sample_content")
                if isinstance(raw_sample, bytes):
                    raw_sample = raw_sample.decode("utf-8")

                if raw_sample:
                    chunk = json.loads(raw_sample)
                    source = chunk.get("source", "")
                    metadata = dict(chunk.get("metadata", {}))
                else:
                    source = ""
                    metadata = {}

                summaries.append(
                    DocumentSummary(
                        document_id=self._decode_tag_value(document_id),
                        source=source,
                        chunk_count=chunk_count,
                        metadata=metadata,
                    ),
                )

            if res.cursor is None or res.cursor.cid == 0:
                break
            res = await ft.aggregate(  # type: ignore[no-untyped-call]
                Cursor(res.cursor.cid),
            )
        return summaries

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _response_error() -> type[BaseException]:
        """Lazy-import ``ResponseError`` so ``redis`` stays optional at
        module load time."""
        from redis.exceptions import ResponseError

        return ResponseError

    @staticmethod
    def _metadata_field_name(key: str) -> str:
        """Return a collision-resistant RediSearch TAG field name.

        Hashing preserves the distinction between arbitrary metadata
        dictionary keys while limiting the field name to ASCII characters.
        """
        return "md_" + hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_tag_value(value: str) -> str:
        """Encode a tag value without separators or query metacharacters."""
        return (
            base64.urlsafe_b64encode(value.encode("utf-8"))
            .decode(
                "ascii",
            )
            .rstrip("=")
        )

    @staticmethod
    def _decode_tag_value(value: str) -> str:
        """Decode a value produced by :meth:`_encode_tag_value`."""
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding).decode("utf-8")

    @staticmethod
    def _serialize_metadata_value(value: object) -> str:
        """Serialize a metadata value for storage in a TAG hash field.

        redis-py's encoder raises ``DataError`` on raw ``bool`` values,
        so booleans are mapped to ``"true"`` / ``"false"`` explicitly.
        Non-scalar values are JSON-serialised.

        Args:
            value (`object`):
                The chunk metadata value to serialize.

        Returns:
            `str`: The string representation.
        """
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _build_metadata_filter(
        metadata_filter: dict[str, Any] | None,
    ) -> str | None:
        """Translate a flat ``{key: value}`` filter into a RediSearch
        hybrid-query prefix string.

        Each pair becomes a ``@md_<key>:{<encoded_value>}`` TAG clause.
        Clauses are joined with spaces (implicit AND).  The result is
        wrapped in parentheses and trailed by a space so it slots
        directly before ``=>`` in a KNN query.

        Returns ``None`` when ``metadata_filter`` is empty.

        Args:
            metadata_filter (`dict[str, Any] | None`):
                The flat filter, or ``None`` for no filter.

        Returns:
            `str | None`: The parenthesised filter string, or ``None``.
        """
        if not metadata_filter:
            return None
        clauses = []
        for key, value in metadata_filter.items():
            field = RedisStore._metadata_field_name(key)
            encoded_value = RedisStore._encode_tag_value(
                RedisStore._serialize_metadata_value(value),
            )
            clauses.append(f"@{field}:{{{encoded_value}}}")
        return "(" + " ".join(clauses) + ") "

    async def _ensure_metadata_fields(
        self,
        collection: str,
        keys: set[str],
    ) -> None:
        """Register metadata keys as TAG fields in the RediSearch index
        schema (lazy ``FT.ALTER``), if they haven't already been added.

        Uses a per-collection cache so the ALTER is issued at most once
        per key.  Racing insert calls that try to add the same field
        concurrently are tolerated by swallowing the "field already
        exists" error.

        Args:
            collection (`str`):
                The target index name.
            keys (`set[str]`):
                The metadata keys to ensure in the schema.
        """
        known = self._metadata_fields.setdefault(collection, set())
        new_keys = {k for k in keys if k not in known}
        if not new_keys:
            return

        from redis.commands.search.field import TagField

        client = self.get_client()
        ft = client.ft(collection)
        # Deterministic order so repeated runs produce the same
        # ALTER sequence.
        for key in sorted(new_keys):
            try:
                await ft.alter_schema_add(  # type: ignore[no-untyped-call]
                    TagField(  # type: ignore[no-untyped-call]
                        RedisStore._metadata_field_name(key),
                        separator="\x1f",
                    ),
                )
            except self._response_error() as exc:
                # A concurrent insert may have created this exact field.
                # Other FT.ALTER failures must remain visible; otherwise we
                # would write data that cannot be filtered.
                if "already exists" not in str(exc).lower():
                    raise
            known.add(key)
