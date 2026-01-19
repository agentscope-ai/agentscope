# -*- coding: utf-8 -*-
"""The MongoDB vector store implementation using MongoDB Vector Search.

This implementation provides a vector database store using MongoDB's vector
 search capabilities. It requires MongoDB with vector search support and
 automatically creates vector search indexes.
"""
import asyncio
import time

from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata
from ...types import Embedding

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient
else:
    AsyncMongoClient = "pymongo.AsyncMongoClient"


class MongoDBStore(VDBStoreBase):
    """MongoDB vector store using MongoDB Vector Search.

    This class provides a vector database store implementation using MongoDB's
    vector search capabilities. It requires MongoDB with vector search support
    and creates vector search indexes automatically.

    .. note:: Ensure your MongoDB instance supports Vector Search
    functionality.

    .. note:: The store automatically creates database, collection, and vector
    search index on first operation. No manual initialization is required.
    """

    def __init__(
        self,
        host: str,
        db_name: str,
        collection_name: str,
        dimensions: int,
        index_name: str = "vector_index",
        distance: Literal["cosine", "euclidean", "dotProduct"] = "cosine",
        filter_fields: list[str] | None = None,
        client_kwargs: dict[str, Any] | None = None,
        db_kwargs: dict[str, Any] | None = None,
        collection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the MongoDB vector store.

        Args:
            host (`str`):
                MongoDB connection host, e.g., "mongodb://localhost:27017" or
                "mongodb+srv://cluster.mongodb.net/".
            db_name (`str`):
                Database name to store vector documents.
            collection_name (`str`):
                Collection name to store vector documents.
            dimensions (`int`):
                Embedding dimensions for the vector search index.
            index_name (`str`, defaults to "vector_index"):
                Vector search index name.
            distance (`Literal["cosine", "euclidean", "dotProduct"]`, \
            defaults to "cosine"):
                Distance metric for vector similarity. Can be one of "cosine",
                "euclidean", or "dotProduct".
            filter_fields (`list[str] | None`, optional):
                List of field paths to index for filtering in $vectorSearch.
                For example: ["payload.doc_id", "payload.chunk_id"].
                These fields can then be used in the `filter` parameter of
                the `search` method. MongoDB $vectorSearch filter supports:
                $gt, $gte, $lt, $lte, $eq, $ne, $in, $nin, $exists, $not.
            client_kwargs (`dict[str, Any] | None`, optional):
                Additional kwargs for MongoDB client.
            db_kwargs (`dict[str, Any] | None`, optional):
                Additional kwargs for database.
            collection_kwargs (`dict[str, Any] | None`, optional):
                Additional kwargs for collection.

        Raises:
            ImportError: If pymongo is not installed.
        """
        try:
            from pymongo import AsyncMongoClient
        except ImportError as e:
            raise ImportError(
                "Please install the latest pymongo package to use "
                "AsyncMongoClient: `pip install pymongo`",
            ) from e

        self._client: AsyncMongoClient = AsyncMongoClient(
            host,
            **(client_kwargs or {}),
        )
        self.db_name = db_name
        self.collection_name = collection_name
        self.index_name = index_name
        self.dimensions = dimensions
        self.distance = distance
        self.filter_fields = filter_fields or []
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
                **self.collection_kwargs,
            )

        from pymongo.operations import SearchIndexModel

        # Build index fields: vector field + optional filter fields
        index_fields: list[dict[str, Any]] = [
            {
                "type": "vector",
                "path": "vector",
                "similarity": self.distance,
                "numDimensions": self.dimensions,
            },
        ]

        # Add user-specified filter fields
        for field_path in self.filter_fields:
            index_fields.append(
                {
                    "type": "filter",
                    "path": field_path,
                },
            )

        search_index_model = SearchIndexModel(
            definition={"fields": index_fields},
            name=self.index_name,
            type="vectorSearch",
        )

        await self._collection.create_search_index(
            model=search_index_model,
        )

    async def _wait_for_index_ready(self, timeout: int = 30) -> None:
        """Wait for the vector search index to be ready with timeout
        protection.

        Args:
            timeout (`int`, defaults to 30):
                Maximum time to wait in seconds.

        Raises:
            TimeoutError: If index is not ready within the timeout period.
        """

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                indices = []
                async for idx in await self._collection.list_search_indexes(
                    self.index_name,
                ):
                    indices.append(idx)
                if indices and indices[0].get("queryable") is True:
                    return
            except Exception:
                pass

            await asyncio.sleep(0.2)

        raise TimeoutError(f"Index not ready after {timeout} seconds")

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Insert documents with embeddings into MongoDB.

        This method automatically creates the database, collection, and vector
        search index if they don't exist.

        Args:
            documents (`list[Document]`):
                List of Document objects to insert.
            **kwargs (`Any`):
                Additional arguments (unused).

        .. note::
            Each inserted record has structure:

            .. code-block:: python

                {
                    "id": str,                # Document ID
                    "vector": list[float],    # Vector embedding
                    "payload": dict,          # DocMetadata as dict
                }
        """
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
        if not docs_to_insert:
            return
        from pymongo import ReplaceOne

        operations = [
            ReplaceOne(
                {"id": doc_record["id"]},
                doc_record,
                upsert=True,
            )
            for doc_record in docs_to_insert
        ]
        await self._collection.bulk_write(operations)

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents using MongoDB Vector Search.

        This method uses MongoDB's $vectorSearch aggregation pipeline for
        vector similarity search. It automatically waits for the vector search
        index to be ready before performing the search.

        Args:
            query_embedding (`Embedding`):
                The embedding vector to search for.
            limit (`int`):
                Maximum number of documents to return.
            score_threshold (`float | None`, optional):
                Minimum similarity score threshold. Documents with scores below
                this threshold will be filtered out.
            **kwargs (`Any`):
                Additional arguments for the search operation.

        Returns:
            `list[Document]`: List of Document objects with embedding,
            score, and metadata.

        .. note::
            - Requires MongoDB with vector search support
            - Uses $vectorSearch aggregation pipeline
        """
        await self._validate_db_and_collection()
        # Wait for index to be ready before searching
        await self._wait_for_index_ready()

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
                    **kwargs,
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
        ids: str | list[str] | None = None,
    ) -> None:
        """Delete documents from the MongoDB collection.

        Args:
            ids (`str | list[str] | None`, optional):
                List of document IDs to delete. If provided, deletes documents
                with matching doc_id in payload.
        """

        if not ids:
            return

        if isinstance(ids, str):
            ids = [ids]

        await self._collection.delete_many({"payload.doc_id": {"$in": ids}})

    def get_client(self) -> AsyncMongoClient:
        """Get the underlying MongoDB client for advanced operations.

        Returns:
            `AsyncMongoClient`: The AsyncMongoClient instance.
        """
        return self._client

    async def delete_collection(self) -> None:
        """Delete the entire collection.

        .. warning::
            This will permanently delete all documents in the collection.
        """
        await self._collection.drop()

    async def delete_database(self) -> None:
        """Delete the entire database.

        .. warning::
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
