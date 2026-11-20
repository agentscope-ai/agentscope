# -*- coding: utf-8 -*-
"""Resolve hosted document IDs to their last fully published vectors."""

import asyncio
from typing import TYPE_CHECKING

from ....rag import KnowledgeBase
from ....rag._vdb import DocumentSummary, VectorSearchResult

if TYPE_CHECKING:
    from ....rag import Chunk
    from ...storage import StorageBase


class PublishedKnowledge(KnowledgeBase):
    """A read handle that follows each document's published vector slot."""

    def __init__(
        self,
        knowledge: KnowledgeBase,
        storage: "StorageBase",
        user_id: str,
        knowledge_base_id: str,
    ) -> None:
        """Keep the runtime scope and resolve versions on each read."""
        super().__init__(
            knowledge.name,
            knowledge.description,
            knowledge.embedding_model,
            knowledge.vector_store,
            knowledge.collection,
            knowledge.metadata_filter,
        )
        self._storage = storage
        self._owner = user_id
        self._kb_id = knowledge_base_id

    async def _published_id(self, document_id: str) -> str:
        record = await self._storage.get_knowledge_document(
            self._owner,
            self._kb_id,
            document_id,
        )
        # Direct SDK inserts have no upload record and use their original ID.
        return record.data.vector_id(document_id) if record else document_id

    async def _visible_ids(self, ids: set[str]) -> dict[str, str]:
        # Uploaded IDs are UUID hex. The alternate slot has a reserved
        # prefix, while the original slot also reads pre-versioning data.
        logical = sorted({key.removeprefix("~") for key in ids})
        active = await asyncio.gather(
            *(self._published_id(key) for key in logical),
        )
        return dict(zip(active, logical))

    async def _search_vector(
        self,
        vector: list[float],
        top_k: int,
    ) -> list[VectorSearchResult]:
        hits = await super()._search_vector(vector, top_k)
        excluded: set[str] = set()
        while hits:
            ids = {hit.document_id for hit in hits}
            active = await self._visible_ids(ids)
            hidden = ids.difference(active)
            if not hidden:
                return [
                    hit.model_copy(
                        update={"document_id": active[hit.document_id]},
                    )
                    for hit in hits
                ]
            excluded.update(hidden)
            # Exclude whole staging documents before ranking. Even a large
            # failed upload cannot consume the backend's candidate limit.
            hits = await self.vector_store.search_excluding(
                self.collection,
                vector,
                top_k,
                self.metadata_filter,
                exclude_document_ids=excluded,
            )
        return []

    async def list_chunks(
        self,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
    ) -> list["Chunk"]:
        """Read the published page, retrying a concurrent slot switch."""
        published = await self._published_id(document_id)
        chunks = await super().list_chunks(
            published,
            offset=offset,
            limit=limit,
        )
        if not chunks:
            current = await self._published_id(document_id)
            if current != published:
                return await super().list_chunks(
                    current,
                    offset=offset,
                    limit=limit,
                )
        return chunks

    async def insert_document(
        self,
        chunks: list["Chunk"],
        document_id: str | None = None,
        document_metadata: dict | None = None,
    ) -> str:
        """Write through the active slot for direct SDK callers."""
        if document_id is None:
            return await super().insert_document(
                chunks,
                None,
                document_metadata,
            )
        published = await self._published_id(document_id)
        await super().insert_document(chunks, published, document_metadata)
        return document_id

    async def delete_document(self, document_id: str) -> None:
        """Delete the published chunks of a logical document."""
        await super().delete_document(await self._published_id(document_id))

    async def list_documents(self) -> list[DocumentSummary]:
        """Expose one summary per published logical document."""
        docs = await super().list_documents()
        records = await self._storage.list_knowledge_documents(
            self._owner,
            self._kb_id,
        )
        active = {r.id: r.data.vector_id(r.id) for r in records}
        published = []
        for doc in docs:
            logical = doc.document_id.removeprefix("~")
            if doc.document_id == active.get(logical, logical):
                published.append(
                    doc.model_copy(update={"document_id": logical}),
                )
        return published
