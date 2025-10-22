# -*- coding: utf-8 -*-
"""The MongoDB vector store implementation using MongoDB Vector Search.

This implementation provides a vector database store using MongoDB's vector
 search capabilities. It requires MongoDB with vector search support (
 MongoDB 7.0+ or Atlas) and automatically creates vector search indexes.
"""

from __future__ import annotations

from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata
from ...types import Embedding

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient as _AsyncMongoClient
else:
    _AsyncMongoClient = "pymongo.AsyncMongoClient"


class MongoDBStore(VDBStoreBase):
    """MongoDB vector store using MongoDB Vector Search.

    This class provides a vector database store implementation using MongoDB's
    vector search capabilities. It requires MongoDB with vector search support
    (MongoDB 7.0+ or Atlas) and creates vector search indexes automatically.

    Parameters
    ----------
    host : str
        MongoDB connection host, e.g., "mongodb://localhost:27017" or
        "mongodb+srv://cluster.mongodb.net/".
    db_name : str
        Database name to store vector documents.
    collection_name : str
        Collection name to store vector documents.
    dimensions : int
        Embedding dimensions. Used when creating the vector search index.
    index_name : str, default "vector_index"
        The Vector Search index name configured on the collection.
    distance : Literal["cosine", "euclidean", "dotProduct"], default "cosine"
        The distance metric to use for the collection. Can be one of
        "cosine", "euclidean", or "dotProduct".
    client_kwargs : dict[str, Any] | None, default None
        Optional extra kwargs for the MongoDB client.
    db_kwargs : dict[str, Any] | None, default None
        Optional extra kwargs for the database.
    collection_kwargs : dict[str, Any] | None, default None
        Optional extra kwargs for the collection.
    """

    def __init__(
        self,
        host: str,
        db_name: str,
        collection_name: str,
        dimensions: int,
        index_name: str = "vector_index",
        distance: Literal["cosine", "euclidean", "dotProduct"] = "cosine",
        client_kwargs: dict[str, Any] | None = None,
        db_kwargs: dict[str, Any] | None = None,
        collection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the MongoDB vector store.

        Args:
            host: MongoDB connection host.
            db_name: Database name to store vector documents.
            collection_name: Collection name to store vector documents.
            dimensions: Embedding dimensions for the vector search index.
            index_name: Vector search index name. Defaults to "vector_index".
            distance: Distance metric for vector similarity. Defaults to
            "cosine".
            client_kwargs: Additional kwargs for MongoDB client.
            db_kwargs: Additional kwargs for database.
            collection_kwargs: Additional kwargs for collection.

        Raises:
            ImportError: If pymongo is not installed.
        """
        try:
            from pymongo import AsyncMongoClient as _Client
        except Exception as e:  # pragma: no cover - import-time error path
            raise ImportError(
                "Please install the latest pymongo package to use "
                "AsyncMongoClient: `pip install pymongo>=5.0`",
            ) from e

        self._client: _AsyncMongoClient = _Client(
            host,
            **(client_kwargs or {}),
        )
        self.db_name = db_name
        self.collection_name = collection_name
        self.index_name = index_name
        self.dimensions = dimensions
        self.distance = distance
        self.db_kwargs = db_kwargs or {}
        self.collection_kwargs = collection_kwargs or {}

        self._db = None
        self._collection = None

    async def _validate_db_and_collection(self) -> None:
        """Validate the database and collection exist, create if necessary.

        This method ensures the database and collection are available,
         and creates a vector search index for the collection.

        Raises:
            Exception: If database or collection creation fails.
        """
        self._db = self._client.get_database(
            self.db_name,
            **self.db_kwargs,
        )

        if self.collection_name not in await self._db.list_collection_names():
            self._collection = await self._db.create_collection(
                self.collection_name,
            )
        else:
            self._collection = self._db.get_collection(
                self.collection_name,
                self.collection_kwargs,
            )

        from pymongo.operations import SearchIndexModel

        search_index_model = SearchIndexModel(
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "vector",
                        "similarity": self.distance,
                        "numDimensions": self.dimensions,
                    },
                ],
            },
            name=self.index_name,
            type="vectorSearch",
        )

        await self._collection.create_search_index(
            model=search_index_model,
        )

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Insert documents with embeddings into MongoDB.

        Args:
            documents: List of Document objects to insert.
            **kwargs: Additional arguments (unused).

        Note:
            Each inserted record has structure:
            {
                "id": str,                # Document ID
                "vector": list[float],    # Vector embedding
                "payload": dict,          # DocMetadata as dict
            }
        """
        # Validate collection exists
        await self._validate_db_and_collection()

        # Prepare documents for insertion
        docs_to_insert = []
        for doc in documents:
            # Convert DocMetadata to dict for storage
            payload = {
                "doc_id": doc.metadata.doc_id,
                "chunk_id": doc.metadata.chunk_id,
                "total_chunks": doc.metadata.total_chunks,
                "content": doc.metadata.content,
            }

            # Create document record
            doc_record = {
                "id": f"{doc.metadata.doc_id}_{doc.metadata.chunk_id}",
                "vector": doc.embedding,
                "payload": payload,
            }
            docs_to_insert.append(doc_record)

        # Insert documents using upsert to handle duplicates
        for doc_record in docs_to_insert:
            await self._collection.replace_one(
                {"id": doc_record["id"]},
                doc_record,
                upsert=True,
            )

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        filter: dict[str, Any] | None = None,  # pylint: disable=W0622
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents using MongoDB Vector Search.

        This method uses MongoDB's $vectorSearch aggregation pipeline for
        vector similarity search. It requires a vector search index to be
        created on the collection.

        Args:
            query_embedding: The embedding vector to search for.
            limit: Maximum number of documents to return.
            score_threshold: Minimum similarity score threshold. Documents with
                scores below this threshold will be filtered out.
            filter: Optional filter dictionary for the search query.
            **kwargs: Additional arguments for the search operation.

        Returns:
            List of Document objects with embedding, score, and metadata.

        Note:
            - Requires MongoDB with vector search support (MongoDB 7.0+ or
            Atlas)
            - Uses $vectorSearch aggregation pipeline
            - Supports both mongodb://localhost:27017 and
                mongodb+srv://... URIs
        """
        # Construct aggregation pipeline for vector search
        # See: https://www.mongodb.com/docs/atlas/atlas-search/vector-search/
        num_candidates = int(
            kwargs.pop(
                "num_candidates",
                max(
                    100,
                    limit * 20,
                ),
            ),
        )

        pipeline: list[dict[str, Any]] = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": "vector",
                    "queryVector": list(query_embedding),
                    "numCandidates": num_candidates,
                    "limit": limit,
                    "filter": filter or {},
                },
            },
            {
                "$project": {
                    "vector": 1,
                    "payload": 1,
                    "score": {"$meta": "vectorSearchScore"},
                },
            },
        ]

        cursor = await self._collection.aggregate(pipeline)
        results: list[Document] = []
        async for item in cursor:
            score_val = float(item.get("score", 0.0))
            if score_threshold is not None and score_val < score_threshold:
                continue

            payload = item.get("payload", {})
            # Rebuild Document
            metadata = DocMetadata(**payload)

            results.append(
                Document(
                    embedding=[float(x) for x in item.get("vector", [])],
                    score=score_val,
                    metadata=metadata,
                ),
            )

        return results

    async def delete(
        self,
        ids: list[str] | None = None,
        filter: str | None = None,  # pylint: disable=redefined-builtin
        **kwargs: Any,
    ) -> None:
        """Delete documents from the MongoDB collection.

        Args:
            ids: List of document IDs to delete. If provided, deletes documents
                with matching doc_id in payload.
            filter: Filter expression for documents to delete (unused).
            **kwargs: Additional arguments for the delete operation.

        Raises:
            ValueError: If neither ids nor filter is provided.
        """

        if ids is None and filter is None:
            raise ValueError(
                "Either ids or filter_expr must be provided for deletion.",
            )

        for doc_id in ids:
            await self._collection.delete_many({"payload.doc_id": doc_id})

    def get_client(self) -> _AsyncMongoClient:
        """Get the underlying MongoDB client for advanced operations.

        Returns:
            The AsyncMongoClient instance.
        """
        return self._client

    async def delete_collection(self) -> None:
        """Delete the entire collection.

        Warning:
            This will permanently delete all documents in the collection.
        """
        await self._collection.drop()

    async def delete_database(self) -> None:
        """Delete the entire database.

        Warning:
            This will permanently delete the entire database and all its
            collections.
        """
        await self._client.drop_database(self.db_name)

    async def close(self) -> None:
        """Close the MongoDB connection.

        This should be called when the store is no longer needed to properly
        clean up resources.
        """
        await self._client.close()
