# -*- coding: utf-8 -*-
"""The Qdrant local vector store implementation."""
from typing import Any

from .. import VectorRecord
from .._knowledge_base import EmbeddingStoreBase, RetrievalResponse
from ...types import Embedding


class QdrantLocalStore(EmbeddingStoreBase):
    """The Qdrant vector store implementation.

    In Qdrant, we use the ``metadata`` field in ``payload`` to store the
    metadata of the original data."""

    def __init__(
        self,
        location: str,
        collection_name: str,
        collection_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the local Qdrant vector store.

        Args:
            location (`str`):
                The location to store the Qdrant database.
            collection_name (`str`):
                The name of the collection to store the embeddings.
            **kwargs (`Any`):
                Other keyword arguments for the Qdrant client.
        """

        from qdrant_client.local.async_qdrant_local import AsyncQdrantLocal

        self.client = AsyncQdrantLocal(location=location, **kwargs)
        self.collection_name = collection_name
        self.collection_kwargs = collection_kwargs or {}

    async def _validate_collection(self) -> None:
        """Validate the collection exists, if not, create it."""
        if not self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                **self.collection_kwargs,
            )

    async def add(self, embeddings: list[VectorRecord], **kwargs: Any) -> None:
        """Add embeddings to the Qdrant vector store.

        Args:
            embeddings:

        """
        await self._validate_collection()

        from qdrant_client.models import PointStruct

        await self.client.upsert(
            self.collection_name,
            points=[
                PointStruct(
                    id=_.id,
                    vector=_.embedding,
                    payload=_.metadata,
                )
                for _ in embeddings
            ]
        )

    async def retrieve(
        self,
        query: Embedding,
        **kwargs: Any
    ) -> list[RetrievalResponse]:
        """Retrieve relevant embeddings for the given queries.

        Args:
            query (`list[float]`):
                The query embedding vector.

        Returns:
            `list[RetrievalResponse]`:
                The list of relevant retrieval responses.
        """
        res = await self.client.query_points(
            collection_name=self.collection_name,
            query_vector=query,
            **kwargs,
        )

        collected_res = []
        for point in res.points:
            collected_res.append(
                RetrievalResponse(
                    content=point.payload["metadata"],
                    embedding=point.vector,
                    score=point.score,
                )
            )
        return collected_res

    def delete(self, *args, **kwargs) -> None:
        pass

