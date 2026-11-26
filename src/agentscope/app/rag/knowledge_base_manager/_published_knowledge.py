# -*- coding: utf-8 -*-
"""Resolve hosted document IDs to their last fully published vectors."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ....rag import KnowledgeBase
from ....rag._vdb import DocumentSummary, VectorSearchResult
from ._vector_slots import version_knowledge

if TYPE_CHECKING:
    from ....rag import Chunk
    from ...storage import KnowledgeDocumentRecord, StorageBase


@dataclass(frozen=True)
class _DocumentAddress:
    """Physical page location and the upload generation that selected it."""

    handle: KnowledgeBase | None
    physical_id: str
    upload_created_at: datetime | None = None
    reindex_id: str | None = None


@dataclass
class _Publications:
    """One observation of upload records and surviving ownership markers."""

    records: dict[str, "KnowledgeDocumentRecord"]
    expired: set[str]
    retired: set[str]
    queried: set[str] | None

    def visible_id(self, alternate: bool, key: str) -> str | None:
        """Resolve a physical hit using the upload's publication record."""
        if key in self.expired:
            return None
        record = self.records.get(key)
        if record:
            data = record.data
            if (
                data.vector_id(key) == key
                and data.alternate_index == alternate
            ):
                return key
            return None
        if key.startswith("~"):
            owner = self.records.get(key[1:])
            if owner and owner.data.has_legacy_alias:
                if not alternate and owner.data.vector_id(owner.id) == key:
                    return owner.id
                return key if alternate else None
        return key if alternate == (key in self.retired) else None

    def confirms_hit(
        self,
        previous: "_Publications | None",
        lane: bool,
        key: str,
        logical: str,
    ) -> bool:
        """Check ownership on both sides of a reusable or SDK slot read."""
        row = self.records.get(logical)
        if (
            row
            and not row.data.alternate_index
            and row.data.reindex_id is None
        ):
            return True
        if previous is None or (
            previous.queried is not None and key not in previous.queried
        ):
            return False
        if previous.visible_id(lane, key) != logical:
            return False
        prior = previous.records.get(logical)
        if row and prior:
            return row.data.reindex_id == prior.data.reindex_id
        return row is None and prior is None

    def physical_ids(self) -> set[str]:
        """Return physical IDs whose default SDK routing can be overridden."""
        return (
            self.records.keys()
            | self.expired
            | self.retired
            | {
                f"~{row.id}"
                for row in self.records.values()
                if row.data.has_legacy_alias
            }
        )

    def summary_versions(
        self,
        alternate: bool,
    ) -> dict[str, tuple[str, str | None] | None]:
        """Track publication changes that invalidate collection summaries."""
        versions: dict[str, tuple[str, str | None] | None] = {}
        for key in self.physical_ids():
            logical = self.visible_id(alternate, key)
            if logical is None:
                if not alternate:
                    versions[key] = None
            else:
                row = self.records.get(logical)
                if row or alternate:
                    versions[key] = (
                        logical,
                        row.data.reindex_id if row else None,
                    )
        return versions

    def inactive_slots(self, collections: set[bool]) -> set[tuple[bool, str]]:
        """Exclude all inactive or expired uploads in a single refill."""
        ids = self.physical_ids()
        return {
            (lane, key)
            for lane in collections
            for key in ids
            if self.visible_id(lane, key) is None
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

    async def _address(
        self,
        document_id: str,
    ) -> _DocumentAddress:
        view = await self._publications({document_id})
        if document_id in view.expired:
            return _DocumentAddress(None, document_id)
        record = view.records.get(document_id)
        if record:
            data = record.data
            alternate = data.alternate_index and data.slot_layout != "legacy"
            return _DocumentAddress(
                self._versions if alternate else self._primary,
                data.vector_id(document_id),
                record.created_at,
                data.reindex_id,
            )
        if view.visible_id(True, document_id) is not None:
            return _DocumentAddress(self._versions, document_id)
        return _DocumentAddress(self._primary, document_id)

    async def _records(
        self,
        ids: set[str],
    ) -> dict[str, "KnowledgeDocumentRecord"]:
        records = await asyncio.gather(
            *(
                self._storage.get_knowledge_document(
                    self._owner,
                    self._kb_id,
                    key,
                )
                for key in ids
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
        view = await self._publications(ids)
        return any(view.visible_id(True, key) is not None for key in ids)

    async def _publications(
        self,
        ids: set[str],
        *,
        bulk: bool = False,
    ) -> _Publications:
        """Resolve durable ownership for candidates missing a live record."""
        if bulk:
            rows = await self._storage.list_knowledge_documents(
                self._owner,
                self._kb_id,
            )
            records = {row.id: row for row in rows}
            missing = None
        else:
            logical = ids | {key[1:] for key in ids if key.startswith("~")}
            records = await self._records(logical)
            missing = logical - records.keys()
        owned, retired = set(), set()
        if missing is None or missing:
            (
                owned,
                retired,
            ) = await self._storage.get_knowledge_vector_ownership(
                self._owner,
                self._kb_id,
                missing,
            )
        # Live rows describe their own layout. An unclassified expired
        # upload reserves its primary tilde slot just like a known old alias.
        retired = {key for key in retired if key[1:] not in records}
        if bulk or any(key.startswith("~") for key in ids):
            kb = await self._storage.get_knowledge_base(
                self._owner,
                self._kb_id,
            )
            if kb:
                retired = retired | kb.data.retired_vector_aliases
        expired = owned - records.keys()
        return _Publications(records, expired, retired, None if bulk else ids)

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
        view: _Publications | None = None
        before_read: dict[bool, _Publications | None] = {}
        while True:
            for lane in refill:
                before_read[lane] = view
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
            view = await self._publications(ids, bulk=bulk)
            visible = {addr: view.visible_id(*addr) for addr in keys}
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
                if view.visible_id(not lane, key) is not None
            } - refreshed_destinations
            refill.update(destinations)
            refreshed_destinations.update(destinations)
            for (lane, key), _ in ranked:
                logical = visible[lane, key]
                assert logical is not None
                prior = before_read[lane]
                if not view.confirms_hit(prior, lane, key, logical):
                    refill.add(lane)
                    # Approximate search may return a different tied winner.
                    if (
                        prior
                        and prior.queried is not None
                        and key not in prior.queried
                    ):
                        bulk = True
            if not refill:
                return [
                    hit.model_copy(update={"document_id": visible[addr]})
                    for addr, hit in ranked
                ]

            # If the score bound is insufficient, exclude known inactive
            # uploads together after the first targeted refill misses.
            if excluded and not bulk:
                view = await self._publications(ids, bulk=True)
                bulk = True
            excluded.update(hidden)
            if bulk:
                excluded.update(view.inactive_slots(refill))

    async def list_chunks(
        self,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
    ) -> list["Chunk"]:
        """Read the published page, retrying a concurrent slot switch."""
        address = await self._address(document_id)
        while address.handle is not None:
            handle = address.handle
            chunks = []
            if handle is not self._versions or (
                await self.vector_store.has_collection(handle.collection)
            ):
                chunks = await handle.list_chunks(
                    address.physical_id,
                    offset=offset,
                    limit=limit,
                )
            current = await self._address(document_id)
            if current == address:
                return chunks
            address = current
        return []

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
        address = await self._address(document_id)
        if address.handle is None:
            raise ValueError(
                f"Document ID {document_id!r} belongs to an expired upload.",
            )
        await address.handle.insert_document(
            chunks,
            address.physical_id,
            document_metadata,
        )
        return document_id

    async def delete_document(self, document_id: str) -> None:
        """Delete the published chunks of a logical document."""
        address = await self._address(document_id)
        if address.handle is not None:
            await address.handle.delete_document(address.physical_id)

    async def list_documents(self) -> list[DocumentSummary]:
        """Expose one summary per published logical document."""
        view = await self._publications(set(), bulk=True)
        collections = [False, True]
        docs: dict[bool, list[DocumentSummary]] = {}
        while collections:
            for alternate in collections:
                if not alternate:
                    docs[False] = await self._primary.list_documents()
                elif await self.vector_store.has_collection(
                    self._versions.collection,
                ):
                    docs[True] = await self._versions.list_documents()
                else:
                    docs[True] = []
            current = await self._publications(set(), bulk=True)
            collections = [
                lane
                for lane in docs
                if view.summary_versions(lane)
                != current.summary_versions(lane)
            ]
            view = current

        summaries = []
        for lane, documents in docs.items():
            for doc in documents:
                logical = view.visible_id(lane, doc.document_id)
                if logical is not None:
                    summaries.append(
                        doc.model_copy(update={"document_id": logical}),
                    )
        return summaries
