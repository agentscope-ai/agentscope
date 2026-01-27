# -*- coding: utf-8 -*-
"""The OceanBase vector store implementation."""
import asyncio
import json
from typing import Any, Callable, Literal, TYPE_CHECKING

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


# Metric configuration: data-driven approach focusing on exceptions
# Base metric names for pyobvector
_METRIC_NAMES = {
    "COSINE": "cosine",
    "L2": "l2",
    "IP": "inner_product",
}

# Only configure special cases that differ from the base
_SEARCH_METRIC_OVERRIDES = {
    "IP": "ip",  # IP uses "ip" for search to get positive inner product
}

_SCORE_CONVERTERS: dict[str, Callable[[float], float]] = {
    "COSINE": lambda d: 1.0 - d,  # COSINE converts distance to similarity
}


class OceanBaseStore(VDBStoreBase):
    """The OceanBase vector store implementation, supporting OceanBase and
    seekdb via pyobvector."""

    # Field names - using descriptive constants to avoid magic strings
    PRIMARY_FIELD = "id"
    VECTOR_FIELD = "embedding"
    DOC_ID_FIELD = "doc_id"
    CHUNK_ID_FIELD = "chunk_id"
    TOTAL_CHUNKS_FIELD = "total_chunks"
    CONTENT_FIELD = "content"

    # Index configuration
    INDEX_NAME = "vidx"
    INDEX_TYPE = "hnsw"

    def __init__(
        self,
        collection_name: str,
        dimensions: int,
        uri: str = "127.0.0.1:2881",
        user: str = "root@test",
        password: str = "",
        db_name: str = "test",
        distance: Literal["COSINE", "L2", "IP"] = "COSINE",
        client_kwargs: dict[str, Any] | None = None,
        collection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the OceanBase vector store.

        Args:
            collection_name (`str`):
                The name of the collection to store the embeddings.
            dimensions (`int`):
                The dimension of the embeddings.
            uri (`str`, defaults to `"127.0.0.1:2881"`):
                The OceanBase server URI, such as "127.0.0.1:2881".
            user (`str`, defaults to `"root@test"`):
                The username for authentication.
            password (`str`, defaults to `""`):
                The password for authentication.
            db_name (`str`, defaults to `"test"`):
                The database name to connect to.
            distance (`Literal["COSINE", "L2", "IP"]`, defaults to `"COSINE"`):
                The distance metric to use for the collection. Can be one of
                "COSINE", "L2", or "IP".
            client_kwargs (`dict[str, Any] | None`, optional):
                Keyword arguments passed to `pyobvector.MilvusLikeClient`.
                Explicit connection arguments override matching keys here.
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
        client_kwargs = dict(client_kwargs or {})

        self._client = pyobvector.MilvusLikeClient(
            uri=uri,
            user=user,
            password=password,
            db_name=db_name,
            **client_kwargs,
        )

        self.collection_name = collection_name
        self.dimensions = dimensions
        self.distance = distance
        self.collection_kwargs = collection_kwargs or {}
        self._collection_ready = False

    def _get_metric_type(self) -> str:
        """Get the metric type for index creation."""
        return _METRIC_NAMES[self.distance]

    def _get_search_metric_type(self) -> str:
        """Get the search metric type for queries.

        Returns the override value if exists, otherwise uses index metric.
        This allows special handling (e.g., IP uses "ip" for positive values).
        """
        return _SEARCH_METRIC_OVERRIDES.get(
            self.distance,
            self._get_metric_type(),
        )

    async def _validate_collection(self) -> None:
        """Validate the collection exists, if not, create it."""
        if self._collection_ready:
            return

        if await asyncio.to_thread(
            self._client.has_collection,
            self.collection_name,
        ):
            self._collection_ready = True
            return

        collection_kwargs = dict(self.collection_kwargs)

        if "schema" not in collection_kwargs:
            collection_kwargs["schema"] = self._create_schema()

        if "index_params" not in collection_kwargs:
            collection_kwargs["index_params"] = self._create_index_params()

        await asyncio.to_thread(
            self._client.create_collection,
            collection_name=self.collection_name,
            **collection_kwargs,
        )
        self._collection_ready = True

    def _create_schema(self) -> Any:
        """Create the collection schema with all required fields.

        Returns:
            Schema object with primary, vector, and metadata fields configured.
        """
        schema = self._client.create_schema()

        # Primary key field
        schema.add_field(
            field_name=self.PRIMARY_FIELD,
            datatype=self._pyobvector.DataType.VARCHAR,
            is_primary=True,
            auto_id=False,
            max_length=36,
        )

        # Vector field
        schema.add_field(
            field_name=self.VECTOR_FIELD,
            datatype=self._pyobvector.DataType.FLOAT_VECTOR,
            dim=self.dimensions,
        )

        # Metadata fields
        schema.add_field(
            field_name=self.DOC_ID_FIELD,
            datatype=self._pyobvector.DataType.STRING,
        )
        schema.add_field(
            field_name=self.CHUNK_ID_FIELD,
            datatype=self._pyobvector.DataType.INT64,
        )
        schema.add_field(
            field_name=self.TOTAL_CHUNKS_FIELD,
            datatype=self._pyobvector.DataType.INT64,
        )
        schema.add_field(
            field_name=self.CONTENT_FIELD,
            datatype=self._pyobvector.DataType.JSON,
            nullable=True,
        )

        return schema

    def _create_index_params(self) -> Any:
        """Create index parameters for vector search.

        Returns:
            Index parameters configured with HNSW index and appropriate metric.
        """
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name=self.VECTOR_FIELD,
            index_type=self.INDEX_TYPE,
            index_name=self.INDEX_NAME,
            metric_type=self._get_metric_type(),
        )
        return index_params

    @staticmethod
    def _content_to_text(content: Any) -> str:
        """Extract text string from content of various formats."""
        if isinstance(content, str):
            return content
        if isinstance(content, dict) and content.get("type") == "text":
            text = content.get("text", "")
            return text if isinstance(text, str) else ""
        return ""

    @staticmethod
    def _normalize_content(content: Any, fallback_text: str) -> Any:
        """Normalize content to TextBlock format."""
        # Already in correct format
        if isinstance(content, dict) and content.get("type"):
            return content
        # Convert string to TextBlock
        if isinstance(content, str):
            return TextBlock(type="text", text=content)
        # Use fallback
        return TextBlock(type="text", text=fallback_text or "")

    def _document_to_dict(self, doc: Document) -> dict[str, Any]:
        """Convert a Document to a dictionary for insertion.

        Args:
            doc (`Document`):
                Document to convert

        Returns:
            `dict[str, Any]`:
                Dictionary with fields ready for database insertion

        Raises:
            `ValueError`:
                If document embedding is None
        """
        if doc.embedding is None:
            raise ValueError(
                "Document embedding is required for OceanBaseStore.add.",
            )

        # Create unique ID from document metadata
        unique_string = json.dumps(
            {
                "doc_id": doc.metadata.doc_id,
                "chunk_id": doc.metadata.chunk_id,
                "content": doc.metadata.content,
            },
            ensure_ascii=True,
            sort_keys=True,
        )

        return {
            self.PRIMARY_FIELD: _map_text_to_uuid(unique_string),
            self.VECTOR_FIELD: doc.embedding,
            self.DOC_ID_FIELD: doc.metadata.doc_id,
            self.CHUNK_ID_FIELD: doc.metadata.chunk_id,
            self.TOTAL_CHUNKS_FIELD: doc.metadata.total_chunks,
            self.CONTENT_FIELD: doc.metadata.content,
        }

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the OceanBase vector store.

        Args:
            documents (`list[Document]`):
                A list of embedding records to be recorded in the store.
        """
        await self._validate_collection()

        data = [self._document_to_dict(doc) for doc in documents]

        await asyncio.to_thread(
            self._client.insert,
            collection_name=self.collection_name,
            data=data,
            **kwargs,
        )

    @staticmethod
    def _extract_distance(
        row: dict[str, Any],
        output_fields: list[str],
    ) -> float | None:
        """Extract distance value from search result row.

        The distance is stored in an extra field that's not in output_fields.
        """
        extra_keys = [key for key in row if key not in output_fields]
        return row.get(extra_keys[-1]) if extra_keys else None

    def _create_document_from_row(
        self,
        row: dict[str, Any],
        output_fields: list[str],
    ) -> tuple[Document, float | None]:
        """Create a Document from a search result row.

        Args:
            row (`dict[str, Any]`):
                Search result row containing document data
            output_fields (`list[str]`):
                List of fields requested in output

        Returns:
            `tuple[Document, float | None]`:
                Tuple of (Document, score)
        """
        distance = self._extract_distance(row, output_fields)
        score = self._convert_distance_to_score(distance)

        content_value = row.get(self.CONTENT_FIELD)
        content_text = self._content_to_text(content_value)
        content = self._normalize_content(content_value, content_text)

        doc_metadata = DocMetadata(
            content=content,
            doc_id=str(row.get(self.DOC_ID_FIELD, "")),
            chunk_id=int(row.get(self.CHUNK_ID_FIELD) or 0),
            total_chunks=int(row.get(self.TOTAL_CHUNKS_FIELD) or 0),
        )

        return (
            Document(
                embedding=None,
                score=score,
                metadata=doc_metadata,
            ),
            score,
        )

    def _convert_distance_to_score(
        self,
        distance: float | None,
    ) -> float | None:
        """Convert distance value to score based on metric type.

        Args:
            distance (`float | None`):
                Raw distance value from database

        Returns:
            `float | None`:
                Converted score (similarity for COSINE, raw value for others)

        """
        if distance is None:
            return None

        # Apply converter if defined, otherwise return raw value (identity)
        converter = _SCORE_CONVERTERS.get(self.distance)
        return converter(distance) if converter else distance

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
                Note: Score semantics (aligned with Milvus):
                - COSINE: similarity [0,1], higher = more similar
                - L2: Euclidean distance, smaller = more similar
                - IP: inner product, larger = more similar
            **kwargs (`Any`):
                Additional arguments for the search API.
                - flter (`list`): Filter conditions.
                - partition_names (`list[str]`): Partition filter.
                - output_fields (`list[str]`): Fields to include in results.
                - search_params (`dict`): Search parameters.
        """
        await self._validate_collection()

        # Remove unsupported parameter
        kwargs.pop("with_dist", None)

        # Prepare output fields and search parameters
        output_fields = self._prepare_output_fields(
            kwargs.pop("output_fields", None),
        )
        search_params = self._prepare_search_params(
            kwargs.pop("search_params", None),
        )

        results = await asyncio.to_thread(
            self._client.search,
            collection_name=self.collection_name,
            data=query_embedding,
            anns_field=self.VECTOR_FIELD,
            with_dist=True,
            limit=limit,
            output_fields=output_fields,
            search_params=search_params,
            **kwargs,
        )

        # Process results and filter by score threshold
        return self._filter_results_by_threshold(
            results,
            output_fields,
            score_threshold,
        )

    def _prepare_output_fields(
        self,
        output_fields: list[str] | None,
    ) -> list[str]:
        """Prepare output fields ensuring all required fields are included.

        Args:
            output_fields (`list[str] | None`):
                User-specified output fields or None

        Returns:
            `list[str]`:
                List of output fields with required fields included
        """
        required_fields = [
            self.DOC_ID_FIELD,
            self.CHUNK_ID_FIELD,
            self.TOTAL_CHUNKS_FIELD,
            self.CONTENT_FIELD,
        ]

        output_fields = output_fields or required_fields

        # Use dict.fromkeys to preserve order while removing duplicates
        return list(dict.fromkeys(output_fields + required_fields))

    def _prepare_search_params(
        self,
        search_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Prepare search parameters with appropriate metric type.

        Args:
            search_params (`dict[str, Any] | None`):
                User-specified search parameters or None

        Returns:
            `dict[str, Any]`:
                Search parameters dict with metric_type set
        """
        search_params = dict(search_params or {})  # Create a copy
        search_params.setdefault("metric_type", self._get_search_metric_type())
        return search_params

    def _filter_results_by_threshold(
        self,
        results: list[dict[str, Any]],
        output_fields: list[str],
        score_threshold: float | None,
    ) -> list[Document]:
        """Filter search results by score threshold.

        Args:
            results (`list[dict[str, Any]]`):
                Raw search results from database
            output_fields (`list[str]`):
                List of output fields
            score_threshold (`float | None`):
                Minimum score threshold or None

        Returns:
            `list[Document]`:
                List of filtered Document objects
        """
        documents = []
        for row in results:
            doc, score = self._create_document_from_row(row, output_fields)

            if score_threshold is not None and (
                score is None or score < score_threshold
            ):
                continue

            documents.append(doc)

        return documents

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

        await asyncio.to_thread(
            self._client.delete,
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
