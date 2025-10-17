# -*- coding: utf-8 -*-
"""The Milvus Lite vector store implementation."""
from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata

# from ..._utils._common import _map_text_to_uuid
from ...types import Embedding

if TYPE_CHECKING:
    from pymilvus import MilvusClient
else:
    MilvusClient = "pymilvus.MilvusClient"


class MilvusLiteStore(VDBStoreBase):
    """The Milvus Lite vector store implementation, supporting both local and
    remote Milvus instances.

    .. note:: In Milvus Lite, we use the scalar fields to store the metadata,
    including the document ID, chunk ID, and original content. The new
    MilvusClient API is used for simplified operations.

    """

    def __init__(
        self,
        uri: str = "./milvus_demo.db",
        collection_name: str = "demo_collection",
        dimensions: int = 768,
        distance: Literal["COSINE", "L2", "IP"] = "COSINE",
        token: str | None = None,
        client_kwargs: dict[str, Any] | None = None,
        collection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Milvus Lite vector store.

        Args:
            uri (`str`, default to "./milvus_demo.db"):
                The URI of the Milvus instance. For Milvus Lite, use a local
                file path like "./milvus_demo.db". For remote Milvus server,
                use URI like "http://localhost:19530".
            collection_name (`str`, default to "demo_collection"):
                The name of the collection to store the embeddings.
            dimensions (`int`, default to 768):
                The dimension of the embeddings.
            distance (`Literal["COSINE", "L2", "IP"]`, default to "COSINE"):
                The distance metric to use for the collection. Can be one of
                "COSINE", "L2", or "IP". Defaults to "COSINE".
            token (`str | None`, optional):
                The token for authentication when connecting to remote Milvus.
                Format: "username:password". Not needed for Milvus Lite.
            client_kwargs (`dict[str, Any] | None`, optional):
                Other keyword arguments for the Milvus client.
            collection_kwargs (`dict[str, Any] | None`, optional):
                Other keyword arguments for creating the collection.
        """

        try:
            from pymilvus import MilvusClient
        except ImportError as e:
            raise ImportError(
                "Milvus client is not installed. Please install it with "
                "`pip install pymilvus`.",
            ) from e

        client_kwargs = client_kwargs or {}

        # Initialize MilvusClient with uri and optional token
        init_params = {"uri": uri, **client_kwargs}
        if token is not None:
            init_params["token"] = token

        self._client = MilvusClient(**init_params)

        self.collection_name = collection_name
        self.dimensions = dimensions
        self.distance = distance
        self.collection_kwargs = collection_kwargs or {}

    async def _validate_collection(self) -> None:
        """Validate the collection exists, if not, create it."""
        if not self._client.has_collection(self.collection_name):
            # Create collection with the new MilvusClient API
            # By default, it creates an auto-incrementing integer ID field
            self._client.create_collection(
                collection_name=self.collection_name,
                dimension=self.dimensions,
                metric_type=self.distance,
                **self.collection_kwargs,
            )

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the Milvus vector store.

        Args:
            documents (`list[Document]`):
                A list of embedding records to be recorded in the Milvus store.
            **kwargs (`Any`):
                Additional arguments for the insert operation.
        """
        await self._validate_collection()

        # Prepare data for insertion using the new MilvusClient API
        data = []
        for doc in documents:
            # Generate a unique integer ID based on hash
            # Use hash of doc_id + chunk_id to create a stable integer ID
            id_str = f"{doc.metadata.doc_id}_{doc.metadata.chunk_id}"
            unique_id = abs(hash(id_str)) % (
                10**10
            )  # Keep it within reasonable range

            # Prepare data entry with vector and metadata
            entry = {
                "id": unique_id,
                "vector": doc.embedding,
                "doc_id": doc.metadata.doc_id,
                "chunk_id": doc.metadata.chunk_id,
                "content": str(doc.metadata.content),
                "total_chunks": doc.metadata.total_chunks,
            }
            data.append(entry)

        # Insert data using MilvusClient
        self._client.insert(
            collection_name=self.collection_name,
            data=data,
        )

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents from the Milvus vector store.

        Args:
            query_embedding (`Embedding`):
                The embedding of the query text.
            limit (`int`):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, optional):
                The threshold of the score to filter the results.
            **kwargs (`Any`):
                Additional arguments for the Milvus client search API.
                - filter (`str`): Expression to filter the search results.
                - output_fields (`list[str]`): Fields to include in results.
        """
        # Get filter expression if specified
        filter_expr = kwargs.get("filter", None)

        # Get output fields if specified
        output_fields = kwargs.get(
            "output_fields",
            ["doc_id", "chunk_id", "content", "total_chunks"],
        )

        # Execute search using MilvusClient
        results = self._client.search(
            collection_name=self.collection_name,
            data=[query_embedding],
            limit=limit,
            filter=filter_expr,
            output_fields=output_fields,
        )

        # Process results
        collected_res = []
        for hits in results:
            for hit in hits:
                # Check score threshold
                if (
                    score_threshold is not None
                    and hit["distance"] < score_threshold
                ):
                    continue

                # Get metadata from entity
                entity = hit["entity"]
                from ...message import TextBlock

                doc_metadata = DocMetadata(
                    content=TextBlock(text=entity.get("content", "")),
                    doc_id=entity.get("doc_id", ""),
                    chunk_id=entity.get("chunk_id", 0),
                    total_chunks=entity.get("total_chunks", 0),
                )

                # Create Document
                collected_res.append(
                    Document(
                        embedding=None,  # Vector not returned by default
                        score=hit["distance"],
                        metadata=doc_metadata,
                    ),
                )

        return collected_res

    async def delete(
        self,
        ids: list[str] | None = None,
        filter_expr: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Delete documents from the Milvus vector store.

        Args:
            ids (`list[str] | None`, optional):
                List of entity IDs to delete.
            filter_expr (`str | None`, optional):
                Expression to filter documents to delete.
            **kwargs (`Any`):
                Additional arguments for the delete operation.
        """
        if ids is None and filter_expr is None:
            raise ValueError(
                "Either ids or filter_expr must be provided for deletion.",
            )

        # Delete data using MilvusClient
        self._client.delete(
            collection_name=self.collection_name,
            ids=ids,
            filter=filter_expr,
        )

    def get_client(self) -> MilvusClient:
        """Get the underlying Milvus client, so that developers can access
        the full functionality of Milvus.

        Returns:
            `MilvusClient`:
                The underlying Milvus client.
        """
        return self._client
