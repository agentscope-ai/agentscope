# -*- coding: utf-8 -*-
"""Elasticsearch implementation of the vector store backend."""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Literal

from .._document import Chunk
from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch


class ElasticsearchStore(VectorStoreBase):
    """Vector store backed by Elasticsearch dense-vector indexes.

    Each knowledge base maps to one Elasticsearch index.  Vectors use
    cosine similarity and approximate k-nearest-neighbour search.  Chunk
    payloads are retained in ``_source`` while metadata is duplicated into
    a ``flattened`` field so exact-match filters do not create unbounded
    dynamic mappings.

    .. note:: Requires the official async Elasticsearch client. Install it
        with ``pip install agentscope[elasticsearch]``.
    """

    def __init__(
        self,
        hosts: str | list[str],
        *,
        num_candidates: int = 100,
        refresh: bool | Literal["wait_for"] = "wait_for",
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Elasticsearch vector store.

        Args:
            hosts (`str | list[str]`):
                Elasticsearch URL or list of URLs.
            num_candidates (`int`, defaults to ``100``):
                Minimum HNSW candidates considered per shard.  The effective
                value is raised to ``top_k`` when necessary.
            refresh (`bool | Literal["wait_for"]`, defaults to \
            ``"wait_for"``):
                Refresh policy for writes. Set to ``False`` for higher
                indexing throughput when immediate search visibility is not
                required. Elasticsearch's delete-by-query API only accepts a
                boolean, so ``"wait_for"`` maps to ``True`` for deletes.
            client_kwargs (`dict[str, Any] | None`, optional):
                Extra arguments forwarded to ``AsyncElasticsearch`` such as
                ``api_key``, ``basic_auth`` or ``ca_certs``.
        """
        if num_candidates <= 0 or num_candidates > 10_000:
            raise ValueError("num_candidates must be between 1 and 10000")
        self._hosts = hosts
        self._num_candidates = num_candidates
        self._refresh = refresh
        self._client_kwargs = client_kwargs or {}
        self._client: "AsyncElasticsearch | None" = None

    def get_client(self) -> "AsyncElasticsearch":
        """Lazily create and cache the shared async client."""
        if self._client is None:
            from elasticsearch import AsyncElasticsearch

            self._client = AsyncElasticsearch(
                self._hosts,
                **self._client_kwargs,
            )
        return self._client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Close the underlying client if this store created it."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def create_collection(self, name: str, dimensions: int) -> None:
        """Create an Elasticsearch index for one knowledge base."""
        if await self.has_collection(name):
            return
        await self.get_client().indices.create(
            index=name,
            mappings={
                "dynamic": False,
                "properties": {
                    "vector": {
                        "type": "dense_vector",
                        "dims": dimensions,
                        "index": True,
                        "similarity": "cosine",
                    },
                    "document_id": {"type": "keyword"},
                    "chunk": {"type": "object", "enabled": False},
                    "metadata": {"type": "object", "dynamic": "runtime"},
                },
            },
        )

    async def delete_collection(self, name: str) -> None:
        """Delete an Elasticsearch index and all its records."""
        await self.get_client().indices.delete(index=name)

    async def has_collection(self, name: str) -> bool:
        """Return whether an Elasticsearch index exists."""
        return bool(await self.get_client().indices.exists(index=name))

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Bulk-index records using deterministic, retry-safe IDs."""
        if not records:
            return

        operations: list[dict[str, Any]] = []
        for record in records:
            operations.extend(
                [
                    {
                        "index": {
                            "_index": collection,
                            "_id": self._record_id(record),
                        },
                    },
                    {
                        "vector": record.vector,
                        "document_id": record.document_id,
                        "chunk": record.chunk.model_dump(mode="json"),
                        "metadata": record.chunk.metadata,
                    },
                ],
            )

        response = await self.get_client().bulk(
            operations=operations,
            refresh=self._refresh,
        )
        if response.get("errors"):
            failures = [
                item
                for item in response.get("items", [])
                if next(iter(item.values())).get("error")
            ]
            raise RuntimeError(
                f"Elasticsearch bulk insert failed for {len(failures)} "
                "record(s)",
            )

    async def delete(self, collection: str, document_id: str) -> None:
        """Delete every chunk belonging to one source document."""
        await self.get_client().delete_by_query(
            index=collection,
            query={"term": {"document_id": document_id}},
            conflicts="proceed",
            refresh=self._refresh is not False,
        )

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Run an approximate cosine kNN search."""
        if top_k <= 0:
            return []
        if top_k > 10_000:
            raise ValueError("top_k cannot exceed Elasticsearch's 10000 limit")
        num_candidates = min(
            max(self._num_candidates, top_k),
            10_000,
        )
        knn: dict[str, Any] = {
            "field": "vector",
            "query_vector": query_vector,
            "k": top_k,
            "num_candidates": num_candidates,
        }
        filters = self._metadata_filters(metadata_filter)
        if filters:
            knn["filter"] = filters

        response = await self.get_client().search(
            index=collection,
            size=top_k,
            knn=knn,
            source_includes=["document_id", "chunk"],
        )
        return [
            VectorSearchResult(
                score=2.0 * float(hit["_score"]) - 1.0,
                document_id=hit["_source"]["document_id"],
                chunk=Chunk.model_validate(hit["_source"]["chunk"]),
            )
            for hit in response["hits"]["hits"]
        ]

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """Aggregate documents with paginated composite buckets."""
        summaries: list[DocumentSummary] = []
        after: dict[str, Any] | None = None

        while True:
            composite: dict[str, Any] = {
                "size": 500,
                "sources": [
                    {
                        "document_id": {
                            "terms": {"field": "document_id"},
                        },
                    },
                ],
            }
            if after is not None:
                composite["after"] = after

            response = await self.get_client().search(
                index=collection,
                size=0,
                query=self._filter_query(metadata_filter),
                aggs={
                    "documents": {
                        "composite": composite,
                        "aggs": {
                            "sample": {
                                "top_hits": {
                                    "size": 1,
                                    "_source": ["chunk"],
                                },
                            },
                        },
                    },
                },
            )
            aggregation = response["aggregations"]["documents"]
            buckets = aggregation["buckets"]
            for bucket in buckets:
                source = bucket["sample"]["hits"]["hits"][0]["_source"]
                chunk = Chunk.model_validate(source["chunk"])
                summaries.append(
                    DocumentSummary(
                        document_id=bucket["key"]["document_id"],
                        source=chunk.source,
                        chunk_count=bucket["doc_count"],
                        metadata=chunk.metadata,
                    ),
                )
            if not buckets or "after_key" not in aggregation:
                break
            after = aggregation["after_key"]

        return summaries

    @staticmethod
    def _record_id(record: VectorRecord) -> str:
        """Build a stable ID so re-indexing replaces the same chunk."""
        raw = f"{record.document_id}\0{record.chunk.chunk_index}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _metadata_filters(
        metadata_filter: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Translate exact metadata matches into Elasticsearch filters."""
        return [
            {"term": {f"metadata.{key}": value}}
            for key, value in (metadata_filter or {}).items()
        ]

    @classmethod
    def _filter_query(
        cls,
        metadata_filter: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a query for metadata-filtered aggregations."""
        filters = cls._metadata_filters(metadata_filter)
        if not filters:
            return {"match_all": {}}
        return {"bool": {"filter": filters}}
