# -*- coding: utf-8 -*-
"""The Qdrant local vector store implementation."""
from typing import Any

import shortuuid

from .. import Document
from .._knowledge_base import VDBStoreBase


class QdrantLocalStore(VDBStoreBase):
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

        try:
            from qdrant_client.local.async_qdrant_local import AsyncQdrantLocal
        except ImportError as e:
            raise ImportError(
                "Qdrant client is not installed. Please install it with "
                "`pip install qdrant-client`.",
            ) from e

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

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the Qdrant vector store.

        Args:
            documents (`list[Document]`):
                A list of embedding records to be recorded in the Qdrant store.
        """
        await self._validate_collection()

        from qdrant_client.models import PointStruct

        await self.client.upsert(
            self.collection_name,
            points=[
                PointStruct(
                    id=shortuuid.uuid(),
                    vector=_.embedding,
                    payload={

                    },
                )
                for _ in documents
            ],
        )

    async def retrieve(
        self,
        query: list[str],
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant embeddings for the given queries.

        Args:
            query (`list[str]`):
                The list of queries to be queried.
            **kwargs (`Any`):
                Other keyword arguments for the Qdrant client search API.
        """
        res = await self.client.query_points(
            collection_name=self.collection_name,
            query_vector=query,
            **kwargs,
        )

        collected_res = []
        for point in res.points:
            collected_res.append(
                Document(
                    content=point.payload["metadata"],
                    embedding=point.vector,
                    score=point.score,
                ),
            )
        return collected_res

    def delete(self, *args, **kwargs) -> None:
        pass
