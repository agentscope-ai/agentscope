# -*- coding: utf-8 -*-
"""Qdrant implementation of the vector store backend.

Built on the official ``qdrant-client`` SDK using its fully
asynchronous client (:class:`~qdrant_client.AsyncQdrantClient`), so all
operations are non-blocking and safe to call from the application's
event loop.

The same class supports all Qdrant deployment modes through the
constructor arguments:

- ``location=":memory:"`` — in-process, ephemeral (ideal for tests)
- ``path="/path/to/db"`` — in-process, persisted to local disk
- ``url="http://localhost:6333"`` — remote Qdrant server / cloud
"""
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
    from qdrant_client import AsyncQdrantClient


class QdrantStore(VectorStoreBase):
    """Vector store backend backed by `Qdrant <https://qdrant.tech>`_.

    Each knowledge base maps to one Qdrant collection.  Every point
    payload stores the owning ``document_id`` plus the serialized
    :class:`~agentscope.rag.Chunk`, which is reconstructed on
    retrieval.

    .. note:: The ``qdrant-client`` package is required. Install it
        with ``pip install agentscope[vdb-qdrant]``.

    .. code-block:: python

        # In-memory (tests / prototyping)
        store = QdrantStore(location=":memory:")

        # Remote server
        store = QdrantStore(
            url="http://localhost:6333",
            api_key="...",
        )

        async with store:
            await store.create_collection("kb-1", dimensions=768)

    """

    def __init__(
        self,
        location: str | None = None,
        url: str | None = None,
        path: str | None = None,
        api_key: str | None = None,
        distance: Literal["Cosine", "Dot", "Euclid", "Manhattan"] = "Cosine",
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Qdrant vector store.

        Args:
            location (`str | None`, optional):
                Pass ``":memory:"`` for an ephemeral in-process
                instance. Mutually exclusive with ``url`` and ``path``.
            url (`str | None`, optional):
                The URL of a remote Qdrant server, e.g.
                ``"http://localhost:6333"``.
            path (`str | None`, optional):
                A local directory for an in-process, on-disk instance.
            api_key (`str | None`, optional):
                The API key for Qdrant Cloud or a secured server.
            distance (`Literal["Cosine", "Dot", "Euclid", "Manhattan"]`, \
             defaults to ``"Cosine"``):
                The distance metric used when creating collections.
            client_kwargs (`dict[str, Any] | None`, optional):
                Extra keyword arguments forwarded to
                :class:`~qdrant_client.AsyncQdrantClient`.
        """
        self._location = location
        self._url = url
        self._path = path
        self._api_key = api_key
        self._distance = distance
        self._client_kwargs = client_kwargs or {}
        self._client: "AsyncQdrantClient | None" = None

    def get_client(self) -> "AsyncQdrantClient":
        """Lazily create and cache the async Qdrant client.

        Returns:
            `AsyncQdrantClient`:
                The shared async client instance.
        """
        if self._client is None:
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(
                location=self._location,
                url=self._url,
                path=self._path,
                api_key=self._api_key,
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

    async def create_collection(
        self,
        name: str,
        dimensions: int,
    ) -> None:
        """Create a new Qdrant collection.

        No-op if the collection already exists.

        Args:
            name (`str`):
                The collection name. Typically, the knowledge base ID.
            dimensions (`int`):
                The fixed vector dimensionality for this collection.
        """
        from qdrant_client import models

        client = self.get_client()
        if await client.collection_exists(name):
            return
        await client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=dimensions,
                distance=models.Distance(self._distance),
            ),
        )

    async def delete_collection(self, name: str) -> None:
        """Delete a collection and all its data.

        Args:
            name (`str`):
                The collection name to delete.
        """
        await self.get_client().delete_collection(name)

    async def has_collection(self, name: str) -> bool:
        """Check whether a collection exists.

        Args:
            name (`str`):
                The collection name to check.

        Returns:
            `bool`: ``True`` if the collection exists.
        """
        return await self.get_client().collection_exists(name)

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection.

        Each point payload stores the :attr:`VectorRecord.document_id`
        under the ``document_id`` key and the serialized
        :class:`Chunk` under the ``chunk`` key, so that :meth:`delete`
        can remove all records of one document.

        Args:
            collection (`str`):
                The target collection name.
            records (`list[VectorRecord]`):
                The records to insert (each carrying a
                :class:`Chunk` and its embedding vector).
        """

        from qdrant_client import models

        if not records:
            return
        await self.get_client().upsert(
            collection_name=collection,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=record.vector,
                    payload={
                        "document_id": record.document_id,
                        "chunk": record.chunk.model_dump(mode="json"),
                    },
                )
                for record in records
            ],
        )

    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document.

        Matches the ``document_id`` payload key written by
        :meth:`insert` from :attr:`VectorRecord.document_id`.

        Args:
            collection (`str`):
                The target collection name.
            document_id (`str`):
                The source document ID whose records should be
                removed.
        """
        from qdrant_client import models

        await self.get_client().delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document_id),
                        ),
                    ],
                ),
            ),
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
                in this dict (translated into a Qdrant ``must`` payload
                filter against ``chunk.metadata.<key>``).

        Returns:
            `list[VectorSearchResult]`:
                Results ordered by descending similarity score.
        """
        response = await self.get_client().query_points(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
            with_payload=True,
            query_filter=self._build_metadata_filter(metadata_filter),
        )
        return [
            VectorSearchResult(
                score=point.score,
                document_id=point.payload["document_id"],
                chunk=Chunk.model_validate(point.payload["chunk"]),
            )
            for point in response.points
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

        Scrolls the collection in payload-only mode (vectors disabled)
        and aggregates by ``document_id``.  The first chunk encountered
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
        client = self.get_client()
        query_filter = self._build_metadata_filter(metadata_filter)
        summaries: dict[str, DocumentSummary] = {}
        offset: Any = None

        while True:
            points, next_offset = await client.scroll(
                collection_name=collection,
                scroll_filter=query_filter,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                doc_id = point.payload["document_id"]
                summary = summaries.get(doc_id)
                if summary is None:
                    chunk_payload = point.payload["chunk"]
                    summaries[doc_id] = DocumentSummary(
                        document_id=doc_id,
                        source=chunk_payload.get("source", ""),
                        chunk_count=1,
                        metadata=dict(chunk_payload.get("metadata", {})),
                    )
                else:
                    summary.chunk_count += 1
            if next_offset is None:
                break
            offset = next_offset

        return list(summaries.values())

    @staticmethod
    def _build_metadata_filter(
        metadata_filter: dict[str, Any] | None,
    ) -> Any:
        """Translate a flat ``{key: value}`` filter into a Qdrant filter.

        Each ``key`` is matched against the corresponding nested path
        ``chunk.metadata.<key>`` written by :meth:`insert`.  Returns
        ``None`` when ``metadata_filter`` is empty so that callers
        skip the filter argument entirely.

        Args:
            metadata_filter (`dict[str, Any] | None`):
                The flat filter, or ``None`` for no filter.

        Returns:
            `qdrant_client.models.Filter | None`:
                A Qdrant ``Filter`` object, or ``None``.
        """
        if not metadata_filter:
            return None

        from qdrant_client import models

        return models.Filter(
            must=[
                models.FieldCondition(
                    key=f"chunk.metadata.{key}",
                    match=models.MatchValue(value=value),
                )
                for key, value in metadata_filter.items()
            ],
        )
