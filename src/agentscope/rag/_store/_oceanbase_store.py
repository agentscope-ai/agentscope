# -*- coding: utf-8 -*-
"""The OceanBase vector store implementation."""
import json
from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata
from ..._utils._common import _map_text_to_uuid
from ...message import TextBlock
from ...types import Embedding

if TYPE_CHECKING:
    from pyobvector import MilvusLikeClient
else:
    MilvusLikeClient = "pyobvector.MilvusLikeClient"


class OceanBaseStore(VDBStoreBase):
    """The OceanBase vector store implementation, supporting OceanBase and
    SeekDB via pyobvector."""

    def __init__(
        self,
        collection_name: str,
        dimensions: int,
        distance: Literal["l2", "cosine", "inner_product"] = "cosine",
        client_kwargs: dict[str, Any] | None = None,
        collection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the OceanBase vector store.

        Args:
            collection_name (`str`):
                The name of the collection to store the embeddings.
            dimensions (`int`):
                The dimension of the embeddings.
            distance (`Literal["l2", "cosine", "inner_product"]`, default to \
            "cosine"):
                The distance metric to use for the collection.
            client_kwargs (`dict[str, Any] | None`, optional):
                Keyword arguments passed to `pyobvector.MilvusLikeClient`.
            collection_kwargs (`dict[str, Any] | None`, optional):
                Keyword arguments passed to `create_collection`.
        """
        try:
            import pyobvector
        except ImportError as e:
            raise ImportError(
                "OceanBase client is not installed. Please install it with "
                "`pip install pyobvector`.",
            ) from e

        self._pyobvector = pyobvector
        self._client = pyobvector.MilvusLikeClient(**(client_kwargs or {}))

        self.collection_name = collection_name
        self.dimensions = dimensions
        self.distance = distance.lower()
        self.collection_kwargs = collection_kwargs or {}
        self._collection_ready = False

        self._primary_field = "id"
        self._vector_field = "embedding"
        self._doc_id_field = "doc_id"
        self._chunk_id_field = "chunk_id"
        self._total_chunks_field = "total_chunks"
        self._content_field = "content"
        self._index_name = "vidx"
        self._index_type = "hnsw"

    def _index_metric_type(self) -> str:
        if self.distance == "l2":
            return "l2"
        if self.distance == "cosine":
            return "cosine"
        if self.distance in ("inner_product", "ip"):
            return "inner_product"
        raise ValueError(f"Unsupported distance metric: {self.distance}")

    def _search_metric_type(self) -> str:
        if self.distance == "l2":
            return "l2"
        if self.distance == "cosine":
            return "cosine"
        if self.distance in ("inner_product", "ip"):
            return "neg_ip"
        raise ValueError(f"Unsupported distance metric: {self.distance}")

    def _distance_to_score(self, distance: float | None) -> float | None:
        if distance is None:
            return None
        if self.distance == "l2":
            return distance
        if self.distance == "cosine":
            return 1.0 - distance
        if self.distance in ("inner_product", "ip"):
            return -distance
        raise ValueError(f"Unsupported distance metric: {self.distance}")

    async def _validate_collection(self) -> None:
        """Validate the collection exists, if not, create it."""
        if self._collection_ready:
            return

        if self._client.has_collection(self.collection_name):
            self._collection_ready = True
            return

        collection_kwargs = dict(self.collection_kwargs)

        if "schema" not in collection_kwargs:
            schema = self._client.create_schema()
            schema.add_field(
                field_name=self._primary_field,
                datatype=self._pyobvector.DataType.VARCHAR,
                is_primary=True,
                auto_id=False,
                max_length=36,
            )
            schema.add_field(
                field_name=self._vector_field,
                datatype=self._pyobvector.DataType.FLOAT_VECTOR,
                dim=self.dimensions,
            )
            schema.add_field(
                field_name=self._doc_id_field,
                datatype=self._pyobvector.DataType.STRING,
            )
            schema.add_field(
                field_name=self._chunk_id_field,
                datatype=self._pyobvector.DataType.INT64,
            )
            schema.add_field(
                field_name=self._total_chunks_field,
                datatype=self._pyobvector.DataType.INT64,
            )
            schema.add_field(
                field_name=self._content_field,
                datatype=self._pyobvector.DataType.JSON,
                nullable=True,
            )
            collection_kwargs["schema"] = schema

        if "index_params" not in collection_kwargs:
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name=self._vector_field,
                index_type=self._index_type,
                index_name=self._index_name,
                metric_type=self._index_metric_type(),
            )
            collection_kwargs["index_params"] = index_params

        self._client.create_collection(
            collection_name=self.collection_name,
            **collection_kwargs,
        )
        self._collection_ready = True

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, dict):
            if content.get("type") == "text":
                text = content.get("text")
                if isinstance(text, str):
                    return text
        if isinstance(content, str):
            return content
        return ""

    @staticmethod
    def _normalize_content(content: Any, fallback_text: str) -> Any:
        if isinstance(content, dict) and content.get("type"):
            return content
        if isinstance(content, str):
            return TextBlock(type="text", text=content)
        if fallback_text:
            return TextBlock(type="text", text=fallback_text)
        return TextBlock(type="text", text="")

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the OceanBase vector store.

        Args:
            documents (`list[Document]`):
                A list of embedding records to be recorded in the store.
        """
        await self._validate_collection()

        data: list[dict[str, Any]] = []
        for doc in documents:
            if doc.embedding is None:
                raise ValueError(
                    "Document embedding is required for OceanBaseStore.add.",
                )

            unique_string = json.dumps(
                {
                    "doc_id": doc.metadata.doc_id,
                    "chunk_id": doc.metadata.chunk_id,
                    "content": doc.metadata.content,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
            data.append(
                {
                    self._primary_field: _map_text_to_uuid(unique_string),
                    self._vector_field: doc.embedding,
                    self._doc_id_field: doc.metadata.doc_id,
                    self._chunk_id_field: doc.metadata.chunk_id,
                    self._total_chunks_field: doc.metadata.total_chunks,
                    self._content_field: doc.metadata.content,
                },
            )

        self._client.insert(
            collection_name=self.collection_name,
            data=data,
            **kwargs,
        )

    @staticmethod
    def _extract_distance(
        row: dict[str, Any],
        output_fields: list[str],
    ) -> float | None:
        extra_keys = [key for key in row.keys() if key not in output_fields]
        if not extra_keys:
            return None
        return row.get(extra_keys[-1])

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents from the OceanBase vector store.

        Args:
            query_embedding (`Embedding`):
                The embedding of the query text.
            limit (`int`):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, optional):
                The threshold of the score to filter the results.
            **kwargs (`Any`):
                Additional arguments for the search API.
                - flter (`list`): Filter conditions.
                - partition_names (`list[str]`): Partition filter.
                - output_fields (`list[str]`): Fields to include in results.
                - search_params (`dict`): Search parameters.

            Note:
                The returned score aligns with Milvus semantics for cosine/IP.
        """
        await self._validate_collection()

        kwargs.pop("with_dist", None)
        output_fields = kwargs.pop("output_fields", None)
        if output_fields is None:
            output_fields = [
                self._doc_id_field,
                self._chunk_id_field,
                self._total_chunks_field,
                self._content_field,
            ]
        output_fields = list(dict.fromkeys(output_fields))
        for field_name in (
            self._doc_id_field,
            self._chunk_id_field,
            self._total_chunks_field,
            self._content_field,
        ):
            if field_name not in output_fields:
                output_fields.append(field_name)

        search_params = kwargs.pop("search_params", None)
        if search_params is None:
            search_params = {"metric_type": self._search_metric_type()}
        else:
            search_params = dict(search_params)
            search_params.setdefault(
                "metric_type",
                self._search_metric_type(),
            )

        results = self._client.search(
            collection_name=self.collection_name,
            data=query_embedding,
            anns_field=self._vector_field,
            with_dist=True,
            limit=limit,
            output_fields=output_fields,
            search_params=search_params,
            **kwargs,
        )

        collected_res = []
        for row in results:
            distance = self._extract_distance(row, output_fields)
            score = self._distance_to_score(distance)
            if (
                score_threshold is not None
                and score is not None
                and score < score_threshold
            ):
                continue

            content_value = row.get(self._content_field)
            content_text = self._content_to_text(content_value)
            content = self._normalize_content(content_value, content_text)

            doc_metadata = DocMetadata(
                content=content,
                doc_id=str(row.get(self._doc_id_field, "")),
                chunk_id=int(row.get(self._chunk_id_field) or 0),
                total_chunks=int(row.get(self._total_chunks_field) or 0),
            )

            collected_res.append(
                Document(
                    embedding=None,
                    score=score,
                    metadata=doc_metadata,
                ),
            )

        return collected_res

    async def delete(
        self,
        ids: list[str] | None = None,
        where: Any | None = None,
        where_document: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """Delete documents from the OceanBase vector store.

        Args:
            ids (`list[str] | None`, optional):
                List of entity IDs to delete.
            where (`Any | None`, optional):
                Filter conditions for deletion.
            where_document (`Any | None`, optional):
                Unsupported in OceanBaseStore.
        """
        await self._validate_collection()

        if where_document is not None:
            raise ValueError(
                "where_document is not supported for OceanBaseStore.delete.",
            )

        if ids is None and where is None:
            raise ValueError(
                "At least one of ids or where must be provided for deletion.",
            )

        self._client.delete(
            collection_name=self.collection_name,
            ids=ids,
            flter=where,
            **kwargs,
        )

    def get_client(self) -> MilvusLikeClient:
        """Get the underlying OceanBase client, so that developers can access
        the full functionality of OceanBase.

        Returns:
            `MilvusLikeClient`:
                The underlying OceanBase client.
        """
        return self._client
