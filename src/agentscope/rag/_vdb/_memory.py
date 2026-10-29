# -*- coding: utf-8 -*-
"""In-memory implementation of :class:`VectorStoreBase`.

This backend stores all collections and vectors in Python dictionaries.
It uses `numpy` for exact KNN cosine similarity search. It is intended
for testing, quick prototyping, or lightweight execution without the
need to run a separate vector database.
"""
from collections import defaultdict
from typing import Any

import numpy as np

from .._document import Chunk
from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)


class MemoryVectorStore(VectorStoreBase):
    """In-memory vector store backend.

    Stores data in memory and uses NumPy for exact KNN search using
    cosine similarity.
    """

    def __init__(self) -> None:
        """Initialize an empty in-memory vector store."""
        # collection_name -> list of records
        self._collections: dict[str, list[VectorRecord]] = {}
        # collection_name -> dimension
        self._dimensions: dict[str, int] = {}

    async def create_collection(
        self,
        name: str,
        dimensions: int,
    ) -> None:
        """Create a new collection (vector index)."""
        if name not in self._collections:
            self._collections[name] = []
            self._dimensions[name] = dimensions

    async def delete_collection(self, name: str) -> None:
        """Delete a collection and all its data."""
        self._collections.pop(name, None)
        self._dimensions.pop(name, None)

    async def has_collection(self, name: str) -> bool:
        """Check whether a collection exists."""
        return name in self._collections

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection."""
        if collection not in self._collections:
            raise ValueError(f"Collection {collection} does not exist.")

        dim = self._dimensions[collection]
        for record in records:
            if len(record.vector) != dim:
                raise ValueError(
                    f"Vector dimension mismatch. Expected {dim}, "
                    f"got {len(record.vector)}",
                )

        self._collections[collection].extend(records)

    async def delete(
        self,
        collection: str,
        document_id: str,
    ) -> None:
        """Delete all records belonging to one source document."""
        if collection not in self._collections:
            return

        self._collections[collection] = [
            r
            for r in self._collections[collection]
            if r.document_id != document_id
        ]

    def _matches_filter(
        self,
        metadata: dict[str, Any],
        metadata_filter: dict[str, Any] | None,
    ) -> bool:
        if not metadata_filter:
            return True
        for k, v in metadata_filter.items():
            if metadata.get(k) != v:
                return False
        return True

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Find similar records to a query vector via cosine similarity."""
        if collection not in self._collections:
            return []

        records = [
            r
            for r in self._collections[collection]
            if self._matches_filter(r.chunk.metadata, metadata_filter)
        ]

        if not records:
            return []

        # Convert to numpy arrays for fast computation
        db_vectors = np.array([r.vector for r in records])
        q_vector = np.array(query_vector)

        # Compute cosine similarity
        norm_db = np.linalg.norm(db_vectors, axis=1)
        norm_q = np.linalg.norm(q_vector)

        # Avoid division by zero
        with np.errstate(divide="ignore", invalid="ignore"):
            similarities = np.dot(db_vectors, q_vector) / (norm_db * norm_q)
            similarities = np.nan_to_num(similarities, nan=-1.0)

        # Get top_k indices
        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for idx in top_indices:
            results.append(
                VectorSearchResult(
                    score=float(similarities[idx]),
                    document_id=records[idx].document_id,
                    chunk=records[idx].chunk,
                ),
            )

        return results

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List all distinct source documents indexed in a collection."""
        if collection not in self._collections:
            return []

        # Group by document_id
        doc_groups = defaultdict(list)
        for r in self._collections[collection]:
            if self._matches_filter(r.chunk.metadata, metadata_filter):
                doc_groups[r.document_id].append(r)

        summaries = []
        for doc_id, recs in doc_groups.items():
            first_chunk = recs[0].chunk
            summaries.append(
                DocumentSummary(
                    document_id=doc_id,
                    source=first_chunk.source,
                    chunk_count=len(recs),
                    metadata=first_chunk.metadata.copy(),
                ),
            )

        return summaries

    async def list_chunks(
        self,
        collection: str,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """List the chunks of one source document in ``chunk_index`` order."""
        if collection not in self._collections:
            return []

        chunks = [
            r.chunk
            for r in self._collections[collection]
            if r.document_id == document_id
            and self._matches_filter(r.chunk.metadata, metadata_filter)
        ]

        # Sort by chunk_index
        chunks.sort(key=lambda c: c.chunk_index)

        return chunks[offset : offset + limit]
