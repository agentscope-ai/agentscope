# -*- coding: utf-8 -*-
"""The Qdrant local vector store implementation."""
import json
from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata
from ..._utils._common import _map_text_to_uuid
from ...types import Embedding

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient
else:
    AsyncQdrantClient = "qdrant_client.AsyncQdrantClient"


class QdrantStore(VDBStoreBase):
    """The Qdrant vector store implementation, supporting both local and
    remote Qdrant instances.

    .. note:: In Qdrant, we use the ``payload`` field to store the metadata,
    including the document ID, chunk ID, and original content.

    """

    def __init__(
        self,
        location: Literal[":memory:"] | str,
        collection_name: str,
        dimensions: int,
        distance: Literal["Cosine", "Euclid", "Dot", "Manhattan"] = "Cosine",
        client_kwargs: dict[str, Any] | None = None,
        collection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the local Qdrant vector store.

        Args:
            location (`Literal[":memory:"] | str`):
                The location of the Qdrant instance. Use ":memory:" for
                in-memory Qdrant instance, or url for remote Qdrant instance,
                e.g. "http://localhost:6333" or a path to a directory.
            collection_name (`str`):
                The name of the collection to store the embeddings.
            dimensions (`int`):
                The dimension of the embeddings.
            distance (`Literal["Cosine", "Euclid", "Dot", "Manhattan"]`, \
            default to "Cosine"):
                The distance metric to use for the collection. Can be one of
                "Cosine", "Euclid", "Dot", or "Manhattan". Defaults to
                "Cosine".
            client_kwargs (`dict[str, Any] | None`, optional):
                Other keyword arguments for the Qdrant client.
            collection_kwargs (`dict[str, Any] | None`, optional):
                Other keyword arguments for creating the collection.
        """

        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError as e:
            raise ImportError(
                "Qdrant client is not installed. Please install it with "
                "`pip install qdrant-client`.",
            ) from e

        client_kwargs = client_kwargs or {}
        self._client = AsyncQdrantClient(location=location, **client_kwargs)

        self.collection_name = collection_name
        self.dimensions = dimensions
        self.distance = distance
        self.collection_kwargs = collection_kwargs or {}

    async def _validate_collection(self) -> None:
        """Validate the collection exists, if not, create it."""
        if not await self._client.collection_exists(self.collection_name):
            from qdrant_client import models

            collections_kwargs = {
                "collection_name": self.collection_name,
                "vectors_config": models.VectorParams(
                    size=self.dimensions,
                    distance=getattr(models.Distance, self.distance.upper()),
                ),
                **self.collection_kwargs,
            }
            await self._client.create_collection(**collections_kwargs)

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the Qdrant vector store.

        Args:
            documents (`list[Document]`):
                A list of embedding records to be recorded in the Qdrant store.
        """
        await self._validate_collection()

        from qdrant_client.models import PointStruct

        await self._client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=_map_text_to_uuid(
                        json.dumps(
                            {
                                "doc_id": _.metadata.doc_id,
                                "chunk_id": _.metadata.chunk_id,
                                "content": _.metadata.content,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                    vector=_.embedding,
                    payload=_.metadata,
                )
                for _ in documents
            ],
        )

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents from the Qdrant vector store.

        Args:
            query_embedding (`Embedding`):
                The embedding of the query text.
            limit (`int`):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, optional):
                The threshold of the score to filter the results.
            **kwargs (`Any`):
                Other keyword arguments for the Qdrant client search API.
        """
        res = await self._client.query_points(
            collection_name=self.collection_name,
            query=query_embedding,
            limit=limit,
            score_threshold=score_threshold,
            **kwargs,
        )

        collected_res = []
        for point in res.points:
            collected_res.append(
                Document(
                    embedding=point.vector,
                    score=point.score,
                    metadata=DocMetadata(**point.payload),
                ),
            )
        return collected_res

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete is not implemented for QdrantStore."""
        raise NotImplementedError(
            "Delete is not implemented for QdrantStore.",
        )

    def get_client(self) -> AsyncQdrantClient:
        """Get the underlying Qdrant client, so that developers can access
        the full functionality of Qdrant.

        Returns:
            `AsyncQdrantClient`:
                The underlying Qdrant client.
        """
        return self._client
