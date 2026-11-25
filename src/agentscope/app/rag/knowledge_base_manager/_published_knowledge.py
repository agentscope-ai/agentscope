# -*- coding: utf-8 -*-
"""Resolve hosted document IDs to their last fully published vectors."""

import asyncio
from typing import TYPE_CHECKING

from ....rag import KnowledgeBase
from ....rag._vdb import DocumentSummary, VectorSearchResult
from ._vector_slots import version_knowledge

if TYPE_CHECKING:
    from ....rag import Chunk
    from ...storage import KnowledgeDocumentRecord, StorageBase


def _visible_id(
    alternate: bool,
    key: str,
    records: dict[str, "KnowledgeDocumentRecord"],
    retired: set[str],
) -> str | None:
    """Resolve a physical hit using the upload's publication record."""
    record = records.get(key)
    if record:
        data = record.data
        if data.vector_id(key) == key and data.alternate_index == alternate:
            return key
        return None
    if key.startswith("~"):
        owner = records.get(key[1:])
        if owner and owner.data.has_legacy_alias:
            if not alternate and owner.data.vector_id(owner.id) == key:
                return owner.id
            return key if alternate else None
    return key if alternate == (key in retired) else None


def _inactive_slots(
    records: dict[str, "KnowledgeDocumentRecord"],
    retired: set[str],
    collections: set[bool],
) -> set[tuple[bool, str]]:
    """Build document filters for the collections that need more hits."""
    ids = (
        records.keys()
        | retired
        | {
            f"~{row.id}"
            for row in records.values()
            if row.data.has_legacy_alias
        }
    )
    return {
        (lane, key)
        for lane in collections
        for key in ids
        if _visible_id(lane, key, records, retired) is None
    }


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
        self._primary = knowledge
        self._versions = version_knowledge(knowledge)

    async def _retired(self) -> set[str]:
        kb = await self._storage.get_knowledge_base(self._owner, self._kb_id)
        return kb.data.retired_vector_aliases if kb else set()

    async def _address(self, document_id: str) -> tuple[KnowledgeBase, str]:
        record = await self._storage.get_knowledge_document(
            self._owner,
            self._kb_id,
            document_id,
        )
        if record:
            data = record.data
            if data.alternate_index and data.slot_layout != "legacy":
                return self._versions, document_id
            return self._primary, data.vector_id(document_id)
        if document_id.startswith("~"):
            owner = await self._storage.get_knowledge_document(
                self._owner,
                self._kb_id,
                document_id[1:],
            )
            if (
                owner and owner.data.has_legacy_alias
            ) or document_id in await self._retired():
                return self._versions, document_id
        return self._primary, document_id

    async def _records(
        self,
        ids: set[str],
    ) -> dict[str, "KnowledgeDocumentRecord"]:
        logical = ids | {key[1:] for key in ids if key.startswith("~")}
        records = await asyncio.gather(
            *(
                self._storage.get_knowledge_document(
                    self._owner,
                    self._kb_id,
                    key,
                )
                for key in logical
            ),
        )
        return {record.id: record for record in records if record}

    async def _candidates(
        self,
        vector: list[float],
        top_k: int,
        excluded: set[tuple[bool, str]],
        collections: set[bool],
    ) -> dict[bool, list[VectorSearchResult]]:
        """Fetch each collection's best hits with its own exclusions."""
        candidates = {}
        for alternate, handle in (
            (True, self._versions),
            (False, self._primary),
        ):
            if alternate not in collections:
                continue
            if alternate and not await self.vector_store.has_collection(
                handle.collection,
            ):
                candidates[alternate] = []
                continue
            if alternate:
                if (
                    not await self.vector_store.is_search_index_ready(
                        handle.collection,
                    )
                    and not await self._has_published_versions()
                ):
                    candidates[alternate] = []
                    continue
                await handle.ensure_collection()
            omit = {key for lane, key in excluded if lane == alternate}
            if omit:
                hits = await self.vector_store.search_excluding(
                    handle.collection,
                    vector,
                    top_k,
                    self.metadata_filter,
                    exclude_document_ids=omit,
                )
            else:
                hits = await self.vector_store.search(
                    collection=handle.collection,
                    query_vector=vector,
                    top_k=top_k,
                    metadata_filter=self.metadata_filter,
                )
            candidates[alternate] = hits
        return candidates

    async def _has_published_versions(self) -> bool:
        """Check whether an unavailable companion holds any visible rows."""
        docs = await self.vector_store.list_documents(
            self._versions.collection,
            metadata_filter=self.metadata_filter,
        )
        ids = {doc.document_id for doc in docs}
        if not ids:
            return False
        records = await self._records(ids)
        retired = await self._retired()
        return any(
            _visible_id(True, key, records, retired) is not None for key in ids
        )

    async def _all_records(self) -> dict[str, "KnowledgeDocumentRecord"]:
        """Load the current publication map for a bulk exclusion."""
        records = await self._storage.list_knowledge_documents(
            self._owner,
            self._kb_id,
        )
        return {record.id: record for record in records}

    async def _search_vector(
        self,
        vector: list[float],
        top_k: int,
    ) -> list[VectorSearchResult]:
        """Refill only windows that can still improve the published top-k."""
        excluded: set[tuple[bool, str]] = set()
        bulk = False
        retried_publication = False
        refreshed_destinations: set[bool] = set()
        refill = {True, False}
        windows: dict[bool, list[VectorSearchResult]] = {}
        publications: dict[str, tuple[bool, str | None]] = {}
        before_read: dict[bool, dict[str, tuple[bool, str | None]]] = {}
        while True:
            for lane in refill:
                before_read[lane] = publications
            windows.update(
                await self._candidates(vector, top_k, excluded, refill),
            )
            candidates = [
                ((lane, hit.document_id), hit)
                for lane, hits in windows.items()
                for hit in hits
            ]
            keys = {key for key, _ in candidates} | excluded
            ids = {key for _, key in keys}
            records = (
                await self._all_records() if bulk else await self._records(ids)
            )
            publications = {
                key: (row.data.alternate_index, row.data.reindex_id)
                for key, row in records.items()
                if row.data.alternate_index or row.data.reindex_id is not None
            }
            retired = (
                await self._retired()
                if any(key.startswith("~") for key in ids)
                else set()
            )
            visible = {
                (lane, key): _visible_id(lane, key, records, retired)
                for lane, key in keys
            }
            hidden = {addr for addr, _ in candidates if visible[addr] is None}
            published = {
                addr for addr in excluded if visible[addr] is not None
            }
            if published and not retried_publication:
                # A published exclusion may outrank even a full window.
                excluded.clear()
                bulk = False
                refill = {True, False}
                retried_publication = True
                continue

            # On equal scores the version collection comes first. Within a
            # collection the backend's order is already best-first.
            ranked = sorted(
                (item for item in candidates if visible[item[0]] is not None),
                key=lambda item: (item[1].score, item[0][0]),
                reverse=True,
            )[:top_k]
            refill = {True, False} if hidden else set()
            if len(ranked) == top_k:
                last_addr, last_hit = ranked[-1]
                cutoff = (last_hit.score, last_addr[0])
                # Unseen hits cannot outrank the end of their own window.
                refill = {
                    lane
                    for lane, _ in hidden
                    if len(windows[lane]) == top_k
                    and (windows[lane][-1].score, lane) > cutoff
                }
            # A hidden copy can outlive a switch between the two queries.
            # Its destination window may predate the newly published chunks.
            destinations = {
                not lane
                for lane, key in hidden
                if _visible_id(not lane, key, records, retired) is not None
            } - refreshed_destinations
            refill.update(destinations)
            refreshed_destinations.update(destinations)
            # For a reusable slot, bracket the candidate read with the same
            # published slot and rebuild batch. Its score and index can stay
            # identical across writes even when its content changes.
            for (lane, key), _ in ranked:
                logical = visible[lane, key]
                if logical in publications and (
                    before_read[lane].get(logical) != publications[logical]
                ):
                    refill.add(lane)
                    # Approximate search can return different tied documents
                    # on each read. Cover unseen winners together next time.
                    if before_read[lane] and logical not in before_read[lane]:
                        bulk = True
            if not refill:
                return [
                    hit.model_copy(update={"document_id": visible[addr]})
                    for addr, hit in ranked
                ]

            # If the score bound is insufficient, exclude known inactive
            # uploads together after the first targeted refill misses.
            if excluded and not bulk:
                records = await self._all_records()
                retired = await self._retired()
                bulk = True
            excluded.update(hidden)
            if bulk:
                excluded.update(_inactive_slots(records, retired, refill))

    async def list_chunks(
        self,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
    ) -> list["Chunk"]:
        """Read the published page, retrying a concurrent slot switch."""
        handle, published = await self._address(document_id)
        chunks = await handle.list_chunks(
            published,
            offset=offset,
            limit=limit,
        )
        if not chunks:
            current_handle, current = await self._address(document_id)
            if (current_handle, current) != (handle, published):
                return await current_handle.list_chunks(
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
        handle, published = await self._address(document_id)
        await handle.insert_document(chunks, published, document_metadata)
        return document_id

    async def delete_document(self, document_id: str) -> None:
        """Delete the published chunks of a logical document."""
        handle, published = await self._address(document_id)
        await handle.delete_document(published)

    async def list_documents(self) -> list[DocumentSummary]:
        """Expose one summary per published logical document."""
        docs = [(False, doc) for doc in await self._primary.list_documents()]
        if await self.vector_store.has_collection(self._versions.collection):
            docs.extend(
                (True, doc) for doc in await self._versions.list_documents()
            )
        by_id = await self._all_records()
        retired = await self._retired()
        summaries = []
        for alternate, doc in docs:
            logical = _visible_id(alternate, doc.document_id, by_id, retired)
            if logical is not None:
                summaries.append(
                    doc.model_copy(update={"document_id": logical}),
                )
        return summaries
