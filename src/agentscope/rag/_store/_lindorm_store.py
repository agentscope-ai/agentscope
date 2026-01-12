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
    vector engine with vector similarity search and custom routing.

    .. note:: Lindorm uses OpenSearch-compatible API. We store metadata in
    document fields including doc_id, chunk_id, and content.

    """

    def __init__(
        self,
        hosts: list[str],
        index_name: str,
        dimensions: int,
        http_auth: tuple[str, str],
        distance_metric: Literal["l2", "cosine", "inner_product"] = "cosine",
        enable_routing: bool = False,
        use_ssl: bool = False,
        verify_certs: bool = False,
    ) -> None:
        """Initialize the Lindorm vector store.

        Args:
            hosts (`list[str]`):
                List of Lindorm hosts, e.g., ["http://lindorm-host:9200"].
            index_name (`str`):
                The name of the index to store embeddings.
            dimensions (`int`):
                The dimension of the embeddings.
            http_auth (`tuple[str, str]`):
                HTTP authentication (username, password) tuple. Required
                for Aliyun Lindorm cloud service.
            distance_metric (`Literal["l2", "cosine", "inner_product"]`, \
            defaults to "cosine"):
                The distance metric for vector similarity.
            enable_routing (`bool`, defaults to False):
                Whether to enable custom routing for data isolation.
            use_ssl (`bool`, defaults to False):
                Whether to use SSL/TLS for the connection.
            verify_certs (`bool`, defaults to False):
                Whether to verify SSL certificates.
        """

        try:
            from opensearchpy import OpenSearch
        except ImportError as e:
            raise ImportError(
                "opensearch-py is not installed. Please install it with "
                "`pip install opensearch-py`.",
            ) from e

        self._client = OpenSearch(
            hosts=hosts,
            http_auth=http_auth,
            use_ssl=use_ssl,
            verify_certs=verify_certs,
            ssl_show_warn=False,
        )

        self.index_name = index_name
        self.dimensions = dimensions
        self.distance_metric = distance_metric
        self.enable_routing = enable_routing

    def _create_index_body(self) -> dict[str, Any]:
        """Create the index body configuration for Lindorm.

        Returns:
            `dict[str, Any]`:
                The index configuration body including settings and mappings
                for vector storage with Lindorm's lvector engine.
        """
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
            },
        }

        return index_body

    async def _validate_index(self) -> None:
        """Validate the index exists, and create it if not.

        This method checks if the index exists in Lindorm. If the index
        does not exist, it will be created with the appropriate vector
        configuration.

        Raises:
            Exception: If index creation fails due to connection issues
                or invalid configuration.
        """
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
        """
        routing = kwargs.get("routing", None)

        query_body = {
            "size": limit,
            "query": {
                "knn": {
                    "vector": {
                        "vector": query_embedding,
                        "k": limit,
                    },
                },
            },
            "_source": True,
        }

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

    async def delete(
        self,
        doc_ids: list[str],
        routing: str | None = None,
    ) -> None:
        """Delete documents from the Lindorm vector store.

        Args:
            doc_ids (`list[str]`):
                List of internal document UUIDs to delete. These values must
                match the index document IDs generated during :meth:`add` by
                combining ``doc.metadata.doc_id`` and
                ``doc.metadata.chunk_id``, and are not the original
                ``doc.metadata.doc_id`` values.
            routing (`str | None`, optional):
                Custom routing key for targeted deletion when routing is
                enabled. Defaults to None.

        Raises:
            ValueError: If ``doc_ids`` is empty.
        """
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
