# -*- coding: utf-8 -*-
"""MongoDB Atlas / self-hosted vector store backend.
Built on ``pymongo.AsyncMongoClient`` and MongoDB Vector Search
(``$vectorSearch``).  One :class:`MongoDBStore` instance is shared
across the application; each knowledge base maps to one MongoDB
collection inside a single database.
"""
import asyncio
import time
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
    from pymongo import AsyncMongoClient

class MongoDBStore(VectorStoreBase):
    """Vector store backed by MongoDB Vector Search.
    .. note:: Requires ``pymongo`` with ``AsyncMongoClient`` support.
        Install with ``pip install agentscope[mongodb]``.
    """
    def __init__(
        self,
        uri: str,
        database: str,
        distance: Literal["cosine", "euclidean", "dotProduct"] = "cosine",
        index_name: str = "vector_index",          # 每个 collection 共用同名 index
        filter_fields: list[str] | None = None,    # 预声明可过滤字段路径
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._uri = uri
        self._database_name = database
        self._distance = distance
        self._index_name = index_name
        self._filter_fields = filter_fields or [
            "document_id",
            # metadata_filter 常用路径，按需扩展：
            # "chunk.metadata.tenant_id",
            # "chunk.metadata.kb_scope",
        ]
        self._client_kwargs = client_kwargs or {}
        self._client: "AsyncMongoClient | None" = None
        # 缓存：collection_name -> dimensions
        # create_collection 写入，search/insert 可校验
        self._collection_dimensions: dict[str, int] = {}


    def get_client(self) -> "AsyncMongoClient":
        """Lazily create and cache the async MongoDB client.

        Returns:
            `AsyncMongoClient`:
                The shared async client instance.
        """
        if self._client is None:
            from pymongo import AsyncMongoClient

            self._client = AsyncMongoClient(
                self._uri,
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
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------
    def _db(self) -> Any:
        """Return the configured database handle."""
        return self.get_client()[self._database_name]

    def _col(self, collection: str) -> Any:
        """Return a collection handle inside the configured database."""
        return self._db()[collection]



    async def create_collection(self, name: str, dimensions: int) -> None:
        """Create a new MongoDB collection with a vector search index.

        No-op if the collection already exists.

        Args:
            name (`str`):
                The collection name. Typically, the knowledge base ID.
            dimensions (`int`):
                The fixed vector dimensionality for this collection.
        """
        if await self.has_collection(name):
            return

        from pymongo.operations import SearchIndexModel

        await self._db().create_collection(name)
        col = self._col(name)

        fields: list[dict[str, Any]] = [
            {
                "type": "vector",
                "path": "vector",
                "numDimensions": dimensions,
                "similarity": self._distance,
            },
        ]
        fields.extend(
            {"type": "filter", "path": field_path}
            for field_path in self._filter_fields
        )

        model = SearchIndexModel(
            definition={"fields": fields},
            name=self._index_name,
            type="vectorSearch",
        )
        await col.create_search_index(model)
        await self._wait_for_index_ready(col, name)
        self._collection_dimensions[name] = dimensions

    async def delete_collection(self, name: str) -> None:
        """Delete a collection and all its data.

        Args:
            name (`str`):
                The collection name to delete.
        """
        await self._col(name).drop()
        self._collection_dimensions.pop(name, None)

    async def has_collection(self, name: str) -> bool:
        """Check whether a collection exists.

        Args:
            name (`str`):
                The collection name to check.

        Returns:
            `bool`: ``True`` if the collection exists.
        """
        return name in await self._db().list_collection_names()

    async def _wait_for_index_ready(
        self,
        collection: Any,
        collection_name: str,
        timeout: float = 30.0,
    ) -> None:
        """Poll until the vector search index becomes queryable.

        Args:
            collection:
                The MongoDB collection handle.
            collection_name (`str`):
                The collection name (used in timeout error messages).
            timeout (`float`, defaults to ``30.0``):
                Maximum seconds to wait before raising
                :class:`TimeoutError`.

        Raises:
            `TimeoutError`:
                If the index is not queryable within ``timeout`` seconds.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            async for index in await collection.list_search_indexes(
                self._index_name,
            ):
                if index.get("queryable"):
                    return
                break
            await asyncio.sleep(0.5)

        raise TimeoutError(
            f"Vector search index {self._index_name!r} on collection "
            f"{collection_name!r} was not queryable within {timeout}s",
        )

    async def _ensure_index_ready(self, collection: str) -> None:
        """Ensure the vector search index is queryable before reads."""
        await self._wait_for_index_ready(self._col(collection), collection)



    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection.

        Each document stores :attr:`VectorRecord.document_id` under the
        ``document_id`` key and the serialized :class:`Chunk` under the
        ``chunk`` key, so that :meth:`delete` can remove all records of
        one document.

        Args:
            collection (`str`):
                The target collection name.
            records (`list[VectorRecord]`):
                The records to insert (each carrying a
                :class:`Chunk` and its embedding vector).
        """
        if not records:
            return

        from pymongo import ReplaceOne

        col = self._col(collection)
        operations = [
            ReplaceOne(
                {"_id": f"{record.document_id}_{record.chunk.chunk_index}"},
                {
                    "_id": f"{record.document_id}_{record.chunk.chunk_index}",
                    "document_id": record.document_id,
                    "vector": record.vector,
                    "chunk": record.chunk.model_dump(mode="json"),
                },
                upsert=True,
            )
            for record in records
        ]
        await col.bulk_write(operations, ordered=False)

    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document.

        Matches the ``document_id`` field written by :meth:`insert` from
        :attr:`VectorRecord.document_id`.

        Args:
            collection (`str`):
                The target collection name.
            document_id (`str`):
                The source document ID whose records should be
                removed.
        """
        await self._col(collection).delete_many({"document_id": document_id})

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
                in this dict (translated into a MongoDB filter against
                ``chunk.metadata.<key>``).

        Returns:
            `list[VectorSearchResult]`:
                Results ordered by descending similarity score.
        """
        await self._ensure_index_ready(collection)
        col = self._col(collection)

        vector_search: dict[str, Any] = {
            "index": self._index_name,
            "path": "vector",
            "queryVector": query_vector,
            "numCandidates": max(100, top_k * 20),
            "limit": top_k,
        }
        filter_expr = self._build_metadata_filter(metadata_filter)
        if filter_expr is not None:
            vector_search["filter"] = filter_expr

        pipeline = [
            {"$vectorSearch": vector_search},
            {
                "$project": {
                    "document_id": 1,
                    "chunk": 1,
                    "score": {"$meta": "vectorSearchScore"},
                },
            },
        ]

        results: list[VectorSearchResult] = []
        async for doc in await col.aggregate(pipeline):
            results.append(
                VectorSearchResult(
                    score=float(doc["score"]),
                    document_id=doc["document_id"],
                    chunk=Chunk.model_validate(doc["chunk"]),
                ),
            )
        return results

    @staticmethod
    def _build_metadata_filter(
        metadata_filter: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Translate a flat ``{key: value}`` filter into a MongoDB filter.

        Each ``key`` is matched against the corresponding nested path
        ``chunk.metadata.<key>`` written by :meth:`insert`.  Returns
        ``None`` when ``metadata_filter`` is empty so that callers
        skip the filter argument entirely.

        Args:
            metadata_filter (`dict[str, Any] | None`):
                The flat filter, or ``None`` for no filter.

        Returns:
            `dict[str, Any] | None`:
                A MongoDB filter document, or ``None``.
        """
        if not metadata_filter:
            return None

        return {
            "$and": [
                {f"chunk.metadata.{key}": {"$eq": value}}
                for key, value in metadata_filter.items()
            ],
        }

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List all distinct source documents indexed in a collection.

        Aggregates records by ``document_id``.  The first chunk encountered
        for each document supplies the ``source`` filename and the
        document-level ``metadata``.

        Args:
            collection (`str`):
                The target collection name.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict aggregation to records whose
                ``chunk.metadata`` matches every ``key == value`` pair.

        Returns:
            `list[DocumentSummary]`:
                One summary per distinct ``document_id``.
        """
        pipeline: list[dict[str, Any]] = []
        if metadata_filter:
            pipeline.append(
                {
                    "$match": {
                        f"chunk.metadata.{key}": value
                        for key, value in metadata_filter.items()
                    },
                },
            )
        pipeline.append(
            {
                "$group": {
                    "_id": "$document_id",
                    "source": {"$first": "$chunk.source"},
                    "metadata": {"$first": "$chunk.metadata"},
                    "chunk_count": {"$sum": 1},
                },
            },
        )

        summaries: list[DocumentSummary] = []
        async for row in await self._col(collection).aggregate(pipeline):
            summaries.append(
                DocumentSummary(
                    document_id=row["_id"],
                    source=row["source"],
                    chunk_count=row["chunk_count"],
                    metadata=row["metadata"] or {},
                ),
            )
        return summaries
