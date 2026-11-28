# -*- coding: utf-8 -*-
"""Resolve hosted document IDs to their last fully published vectors."""

from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

from ....message import DataBlock, TextBlock
from ....rag import KnowledgeBase
from ....rag._knowledge import _use_vector_search
from ....rag._vdb import DocumentSummary, QdrantStore, VectorSearchResult
from ....rag._vdb._vector_store import (
    _observe_store_searches,
    _RankStages,
    _ReadHits,
    _ReadWindow,
    _StoreQuery,
)
from ._vector_slots import version_knowledge

if TYPE_CHECKING:
    from ....rag import Chunk
    from ...storage import KnowledgeDocumentRecord, StorageBase


def _matches_chunk(original: "Chunk", candidate: "Chunk") -> bool:
    """Allow the ranker to add annotations to an otherwise identical chunk."""
    return (
        original.content == candidate.content
        and original.source == candidate.source
        and original.chunk_index == candidate.chunk_index
        and original.total_chunks == candidate.total_chunks
        and original.metadata.items() <= candidate.metadata.items()
    )


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
        *,
        require_prior: bool = False,
    ) -> bool:
        """Check ownership on both sides of a reusable or SDK slot read."""
        row = self.records.get(logical)
        if (
            not require_prior
            and row
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
        if require_prior:
            return prior == row
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

    @property
    def primary(self) -> KnowledgeBase:
        """Return the original handle selected by the manager."""
        return self._primary

    async def search(
        self,
        queries: list[str | TextBlock | DataBlock],
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list[VectorSearchResult]:
        """Let the selected handle rank candidates from the published slots."""
        search = self._primary.search
        if getattr(search, "__func__", None) is KnowledgeBase.search:
            return await super().search(queries, top_k, score_threshold)

        verified: dict[tuple[str, int], "Chunk"] = {}

        def snapshot(
            hits: list[VectorSearchResult],
        ) -> list[VectorSearchResult]:
            for hit in hits:
                key = (hit.document_id, hit.chunk.chunk_index)
                verified[key] = hit.chunk.model_copy(deep=True)
            # A ranker may mutate hits in place, even with a caching store.
            return [hit.model_copy(deep=True) for hit in hits]

        async def retrieve(
            vector: list[float],
            limit: int,
        ) -> list[VectorSearchResult]:
            with _observe_store_searches(None):
                hits = await self._search_vector(vector, limit)
            return snapshot(hits)

        async def observe(
            collection: str,
            query: _StoreQuery,
        ) -> list[VectorSearchResult]:
            if collection not in (self.collection, self._versions.collection):
                return await query.read()
            alternate = collection == self._versions.collection
            if alternate or not query.is_vector_search:
                return snapshot(
                    await self._published_physical_hits(alternate, query.read),
                )
            requested = query.argument("metadata_filter") or {}
            scope = self.metadata_filter or {}
            if any(
                requested[k] != scope[k]
                for k in requested.keys() & scope.keys()
            ):
                return []
            metadata_filter = {**scope, **requested} or None

            async def publish(
                limit: int,
                read_window: _ReadWindow,
                *,
                observed: dict[tuple[bool, str], _Publications] | None = None,
            ) -> list[VectorSearchResult]:
                return await self._search_vector(
                    query.argument("query_vector"),
                    limit,
                    read_window=read_window,
                    metadata_filter=metadata_filter,
                    observed=observed,
                )

            async def publish_points(
                rank: _RankStages,
                staged: bool,
            ) -> list[VectorSearchResult]:
                if not staged:
                    return snapshot(await rank(publish))
                # A publication can move a prefetched point before rescoring.
                # Recheck only the candidates selected by these stages.
                observed = {}
                while True:
                    hits = await rank(partial(publish, observed=observed))
                    if not observed:
                        return snapshot(hits)
                    current = await self._publications(
                        {key for _, key in observed},
                    )
                    if all(
                        (logical := current.visible_id(lane, key)) is not None
                        and current.confirms_hit(
                            prior,
                            lane,
                            key,
                            logical,
                            require_prior=True,
                        )
                        for (lane, key), prior in observed.items()
                    ):
                        return snapshot(hits)
                    observed.clear()

            if (
                isinstance(query.store, QdrantStore)
                and type(query.store).search is not QdrantStore.search
            ):
                # Let the backend rerank the complete native result page.
                # pylint: disable-next=protected-access
                hits, handled = await query.store._read_published_points(
                    collection,
                    query.read,
                    publish_points,
                )
                if handled:
                    return hits
            if query.forwards_omitted_limit:
                # Let the forwarder apply its own default on the logical
                # collection. Rank and refill at the call receiving that limit.
                forwarded = False

                async def delegated(
                    target: str,
                    inner: _StoreQuery,
                ) -> list[VectorSearchResult]:
                    nonlocal forwarded
                    if target != collection or not inner.is_vector_search:
                        return await inner.read()
                    forwarded = True
                    return await observe(target, inner)

                with _observe_store_searches((None, delegated)):
                    hits = await query.read()
                if forwarded:
                    return hits
            if query.argument("top_k") > 0:
                return snapshot(
                    await publish(query.argument("top_k"), query.window),
                )
            return snapshot(
                await self._published_physical_hits(alternate, query.read),
            )

        with _observe_store_searches(
            (self.vector_store, observe),
        ), _use_vector_search(self._primary, retrieve):
            if score_threshold is None:
                hits = await search(queries=queries, top_k=top_k)
            else:
                hits = await search(
                    queries=queries,
                    top_k=top_k,
                    score_threshold=score_threshold,
                )

        # Independently supplied hits can still match a published page.
        result = []
        for hit in hits:
            key = (hit.document_id, hit.chunk.chunk_index)
            original = verified.get(key)
            if original is None:
                try:
                    chunks = await self.list_chunks(
                        hit.document_id,
                        offset=hit.chunk.chunk_index,
                        limit=1,
                    )
                except NotImplementedError:
                    continue
                if not chunks:
                    continue
                original = chunks[0]
            if _matches_chunk(original, hit.chunk):
                result.append(hit)
        return result

    async def _published_physical_hits(
        self,
        alternate: bool,
        read: _ReadHits,
    ) -> list[VectorSearchResult]:
        """Verify hits in the physical collection queried by the override."""
        revision = await self._storage.get_knowledge_publication_revision(
            self._owner,
            self._kb_id,
            None,
        )
        prior = None
        while True:
            hits = await read()
            if not hits:
                return hits
            ids = {hit.document_id for hit in hits}
            view = await self._publications(ids)
            visible = []
            for hit in hits:
                logical = view.visible_id(alternate, hit.document_id)
                if logical is not None and (
                    (self.metadata_filter or {}).items()
                    <= hit.chunk.metadata.items()
                ):
                    visible.append((logical, hit))
            if revision is not None:
                stable = revision == (
                    await self._storage.get_knowledge_publication_revision(
                        self._owner,
                        self._kb_id,
                        set(view.records),
                    )
                )
                revision = None
            else:
                stable = (
                    prior is not None
                    and (prior.queried is None or ids <= prior.queried)
                    and all(
                        prior.visible_id(alternate, hit.document_id) == logical
                        and prior.records.get(logical)
                        == view.records.get(logical)
                        for logical, hit in visible
                    )
                )
            if stable:
                return [
                    hit.model_copy(update={"document_id": logical})
                    for logical, hit in visible
                ]
            # Bracket a reusable slot or SDK hit with publication reads. A
            # tied winner may change between calls even with the same vector.
            prior = (
                await self._publications(ids, bulk=True)
                if prior is not None
                else view
            )

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

    async def _candidates(
        self,
        vector: list[float],
        top_k: int,
        excluded: set[tuple[bool, str]],
        collections: set[bool],
        read_window: _ReadWindow | None,
        metadata_filter: dict | None,
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
                if not await self.vector_store.is_search_index_ready(
                    handle.collection,
                ) and not await self._has_published_versions(metadata_filter):
                    candidates[alternate] = []
                    continue
                await handle.ensure_collection()
            omit = {key for lane, key in excluded if lane == alternate}
            if read_window is not None:
                hits = await read_window(
                    handle.collection,
                    top_k,
                    metadata_filter,
                    omit,
                )
                if metadata_filter:
                    hits = [
                        hit
                        for hit in hits
                        if metadata_filter.items()
                        <= hit.chunk.metadata.items()
                    ]
            elif omit:
                hits = await self.vector_store.search_excluding(
                    handle.collection,
                    vector,
                    top_k,
                    metadata_filter,
                    exclude_document_ids=omit,
                )
            else:
                hits = await self.vector_store.search(
                    collection=handle.collection,
                    query_vector=vector,
                    top_k=top_k,
                    metadata_filter=metadata_filter,
                )
            candidates[alternate] = hits
        return candidates

    async def _has_published_versions(
        self,
        metadata_filter: dict | None,
    ) -> bool:
        """Check whether an unavailable companion holds any visible rows."""
        docs = await self.vector_store.list_documents(
            self._versions.collection,
            metadata_filter=metadata_filter,
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
            rows = await self._storage.get_knowledge_documents(
                self._owner,
                self._kb_id,
                logical,
            )
            records = {row.id: row for row in rows}
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
        *,
        read_window: _ReadWindow | None = None,
        metadata_filter: dict | None = None,
        observed: dict[tuple[bool, str], _Publications] | None = None,
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
                await self._candidates(
                    vector,
                    top_k,
                    excluded,
                    refill,
                    read_window,
                    self.metadata_filter
                    if read_window is None
                    else metadata_filter,
                ),
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
                if not view.confirms_hit(
                    prior,
                    lane,
                    key,
                    logical,
                    require_prior=read_window is not None,
                ):
                    refill.add(lane)
                    # Approximate search may return a different tied winner.
                    if (
                        prior
                        and prior.queried is not None
                        and key not in prior.queried
                    ):
                        bulk = True
            if not refill:
                if observed is not None:
                    for address, _ in ranked:
                        observed.setdefault(address, view)
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
        revision = await self._storage.get_knowledge_publication_revision(
            self._owner,
            self._kb_id,
            None,
        )
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
            if revision is not None:
                observed = (
                    await self._storage.get_knowledge_publication_revision(
                        self._owner,
                        self._kb_id,
                        set(view.records),
                    )
                )
                if observed == revision:
                    break
                revision = observed
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
