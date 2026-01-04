# -*- coding: utf-8 -*-
"""The Lindorm vector store implementation."""
import json
from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata
from ..._utils._common import _map_text_to_uuid
from ...types import Embedding

if TYPE_CHECKING:
    from opensearchpy import OpenSearch
else:
    OpenSearch = "opensearchpy.OpenSearch"


class LindormStore(VDBStoreBase):
    """The Lindorm vector store implementation, supporting Aliyun Lindorm
    vector engine with hybrid search and custom routing.

    .. note:: Lindorm uses OpenSearch-compatible API. We store metadata in
    document fields including doc_id, chunk_id, and content.

    """

    def __init__(
        self,
        hosts: list[str],
        index_name: str,
        dimensions: int,
        http_auth: tuple[str, str] | None = None,
        use_ssl: bool = False,
        verify_certs: bool = False,
        distance_metric: Literal["l2", "cosine", "inner_product"] = "cosine",
        enable_routing: bool = False,
        enable_hybrid_search: bool = True,
        rrf_rank_constant: int = 2,
        rrf_knn_weight_factor: float = 0.5,
        client_kwargs: dict[str, Any] | None = None,
        index_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Lindorm vector store.

        Args:
            hosts (`list[str]`):
                List of Lindorm hosts, e.g., ["http://lindorm-host:9200"].
            index_name (`str`):
                The name of the index to store embeddings.
            dimensions (`int`):
                The dimension of the embeddings.
            http_auth (`tuple[str, str] | None`, optional):
                HTTP authentication (username, password) tuple.
            use_ssl (`bool`, defaults to True):
                Whether to use SSL/TLS connection.
            verify_certs (`bool`, defaults to True):
                Whether to verify SSL certificates.
            distance_metric (`Literal["l2", "cosine", "inner_product"]`, \
            defaults to "cosine"):
                The distance metric for vector similarity.
            enable_routing (`bool`, defaults to False):
                Whether to enable custom routing for data isolation.
            enable_hybrid_search (`bool`, defaults to False):
                Whether to enable full-text and vector hybrid search.
            rrf_rank_constant (`int`, defaults to 60):
                RRF rank constant for hybrid search fusion.
            rrf_knn_weight_factor (`float`, defaults to 1.0):
                Weight factor for KNN in hybrid search.
            client_kwargs (`dict[str, Any] | None`, optional):
                Additional keyword arguments for OpenSearch client.
            index_kwargs (`dict[str, Any] | None`, optional):
                Additional keyword arguments for index creation.
        """

        try:
            from opensearchpy import OpenSearch
        except ImportError as e:
            raise ImportError(
                "opensearch-py is not installed. Please install it with "
                "`pip install opensearch-py`.",
            ) from e

        client_kwargs = client_kwargs or {}
        self._client = OpenSearch(
            hosts=hosts,
            http_auth=http_auth,
            use_ssl=use_ssl,
            verify_certs=verify_certs,
            **client_kwargs,
        )

        self.index_name = index_name
        self.dimensions = dimensions
        self.distance_metric = distance_metric
        self.enable_routing = enable_routing
        self.enable_hybrid_search = enable_hybrid_search
        self.rrf_rank_constant = rrf_rank_constant
        self.rrf_knn_weight_factor = rrf_knn_weight_factor
        self.index_kwargs = index_kwargs or {}

    def _create_index_body(self) -> dict[str, Any]:
        """Create the index body configuration for Lindorm."""
        knn_settings: dict[str, Any] = {}
        if self.enable_routing:
            knn_settings["knn_routing"] = True

        # Map distance metric to Lindorm's space_type
        space_type_map = {
            "l2": "l2",
            "cosine": "cosinesimil",
            "inner_product": "innerproduct",
        }
        lvector_space_type = space_type_map.get(
            self.distance_metric,
            self.distance_metric,
        )

        index_body = {
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "knn": True,
                    **knn_settings,
                },
                **self.index_kwargs.get("settings", {}),
            },
            "mappings": {
                "_source": {"excludes": ["vector"]},
                "properties": {
                    "vector": {
                        "type": "knn_vector",
                        "dimension": self.dimensions,
                        "method": {
                            "engine": "lvector",
                            "name": "hnsw",
                            "space_type": lvector_space_type,
                        },
                    },
                    "doc_id": {"type": "keyword"},
                    "chunk_id": {"type": "integer"},
                    "content": {"type": "object", "enabled": False},
                    "total_chunks": {"type": "integer"},
                },
                **self.index_kwargs.get("mappings", {}),
            },
        }

        return index_body

    async def _validate_index(self) -> None:
        """Validate the index exists, if not, create it."""
        if not self._client.indices.exists(index=self.index_name):
            index_body = self._create_index_body()
            self._client.indices.create(
                index=self.index_name,
                body=index_body,
            )

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the Lindorm vector store.

        Args:
            documents (`list[Document]`):
                A list of documents to be added to the Lindorm store.
            **kwargs (`Any`):
                Additional arguments:
                - routing (`str`): Custom routing key for data isolation.
        """
        await self._validate_index()

        routing = kwargs.get("routing", None)

        for doc in documents:
            unique_string = json.dumps(
                {
                    "doc_id": doc.metadata.doc_id,
                    "chunk_id": doc.metadata.chunk_id,
                },
                ensure_ascii=False,
            )
            doc_id = _map_text_to_uuid(unique_string)

            body = {
                "vector": doc.embedding,
                "doc_id": doc.metadata.doc_id,
                "chunk_id": doc.metadata.chunk_id,
                "content": doc.metadata.content,
                "total_chunks": doc.metadata.total_chunks,
            }

            index_params: dict[str, Any] = {
                "index": self.index_name,
                "id": doc_id,
                "body": body,
            }

            if self.enable_routing and routing:
                index_params["routing"] = routing

            self._client.index(**index_params)

        self._client.indices.refresh(index=self.index_name)

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents from the Lindorm vector store.

        Args:
            query_embedding (`Embedding`):
                The embedding of the query text.
            limit (`int`):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, optional):
                The threshold of the score to filter results.
            **kwargs (`Any`):
                Additional arguments:
                - routing (`str`): Custom routing key for targeted search.
                - query_text (`str`): Text query for hybrid search.
                - scalar_filters (`list[dict]`): Scalar filters for filtering.
                - filter_type (`str`): Filter type, defaults to \
                "efficient_filter".
        """
        routing = kwargs.get("routing", None)
        query_text = kwargs.get("query_text", None)
        scalar_filters = kwargs.get("scalar_filters", None)
        filter_type = kwargs.get("filter_type", "efficient_filter")

        knn_query: dict[str, Any] = {
            "vector": query_embedding,
            "k": limit,
        }

        if self.enable_hybrid_search and (query_text or scalar_filters):
            filter_conditions = []

            if query_text:
                filter_conditions.append(
                    {
                        "bool": {
                            "must": [
                                {
                                    "match": {
                                        "content": {
                                            "query": query_text,
                                        },
                                    },
                                },
                            ],
                        },
                    },
                )

            if scalar_filters:
                filter_conditions.append(
                    {
                        "bool": {
                            "filter": scalar_filters,
                        },
                    },
                )

            if len(filter_conditions) == 1:
                knn_query["filter"] = filter_conditions[0]
            else:
                knn_query["filter"] = {
                    "bool": {
                        "must": filter_conditions,
                    },
                }

            query_body = {
                "size": limit,
                "query": {
                    "knn": {
                        "vector": knn_query,
                    },
                },
                "ext": {
                    "lvector": {
                        "hybrid_search_type": "filter_rrf",
                        "rrf_rank_constant": str(self.rrf_rank_constant),
                        "rrf_knn_weight_factor": str(self.rrf_knn_weight_factor),
                    },
                },
            }

            if scalar_filters:
                query_body["ext"]["lvector"]["filter_type"] = filter_type
        else:
            query_body = {
                "size": limit,
                "query": {
                    "knn": {
                        "vector": knn_query,
                    },
                },
            }

        # Add _source to retrieve document fields
        query_body["_source"] = True

        search_params: dict[str, Any] = {
            "index": self.index_name,
            "body": query_body,
        }

        if self.enable_routing and routing:
            search_params["routing"] = routing

        response = self._client.search(**search_params)

        collected_res = []
        for hit in response["hits"]["hits"]:
            score = hit["_score"]

            if score_threshold is not None and score < score_threshold:
                continue

            source = hit.get("_source", {})
            if not source:
                # Lindorm might return fields directly without _source
                source = hit

            doc_metadata = DocMetadata(
                content=source.get("content", {}),
                doc_id=source.get("doc_id", ""),
                chunk_id=source.get("chunk_id", 0),
                total_chunks=source.get("total_chunks", 0),
            )

            collected_res.append(
                Document(
                    embedding=source.get("vector"),
                    score=score,
                    metadata=doc_metadata,
                ),
            )

        return collected_res

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete documents from the Lindorm vector store.

        Args:
            **kwargs (`Any`):
                - doc_ids (`list[str]`): List of document IDs to delete.
                - routing (`str`): Custom routing key.
        """
        doc_ids = kwargs.get("doc_ids", [])
        routing = kwargs.get("routing", None)

        if not doc_ids:
            raise ValueError("doc_ids must be provided for deletion.")

        for doc_id in doc_ids:
            delete_params: dict[str, Any] = {
                "index": self.index_name,
                "id": doc_id,
            }

            if self.enable_routing and routing:
                delete_params["routing"] = routing

            self._client.delete(**delete_params)

        self._client.indices.refresh(index=self.index_name)

    def get_client(self) -> OpenSearch:
        """Get the underlying OpenSearch client for Lindorm.

        Returns:
            `OpenSearch`:
                The underlying OpenSearch client.
        """
        return self._client
