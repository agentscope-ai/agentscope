# -*- coding: utf-8 -*-
"""Abstract base class for vector store backends.

A :class:`VectorStoreBase` instance is the single connection point to
one vector database deployment.  It is created once at application
startup, passed into ``create_app(vector_store=...)``, and shared
across all requests for the lifetime of the process — similar to
:class:`~agentscope.app.storage.StorageBase` and
:class:`~agentscope.app.message_bus.MessageBus`.

Each **knowledge base** maps to one **collection** inside the vector
store.  Collections are isolated: different knowledge bases never
share a collection, so retrieval is always scoped to a single
collection without cross-collection filtering.

Lifecycle is managed via the async context manager protocol
(``__aenter__`` / ``__aexit__``), which the app lifespan calls
automatically.
"""
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial, wraps
from inspect import (
    BoundArguments,
    Parameter,
    Signature,
    iscoroutinefunction,
    isfunction,
    signature,
)
from typing import Any, Self

from pydantic import BaseModel, Field

from .._document import Chunk


class VectorRecord(BaseModel):
    """A single record to insert into a vector store collection.

    Pairs a :class:`Chunk` (the business payload — content, source,
    structural metadata) with its dense embedding vector.  ``Chunk``
    is intentionally not extended with an ``embedding`` field so its
    semantics stay stable across the indexing pipeline; instead the
    vector lives in this wrapper whose only purpose is "I am about
    to be inserted into a vector database."
    """

    vector: list[float]
    """The dense embedding vector for :attr:`chunk`."""

    document_id: str
    """The ID of the source document this record belongs to.
    Assigned by the knowledge base layer when the document is
    registered.  Backends must persist it at insertion time so that
    :meth:`VectorStoreBase.delete` can remove all records of one
    document as a unit."""

    chunk: Chunk
    """The business payload — content, source, structural metadata."""


class VectorSearchResult(BaseModel):
    """A single result returned by a similarity search.

    Pairs the matched :class:`Chunk` with its similarity score.
    ``Chunk`` is intentionally not extended with a ``score`` field so
    its semantics stay stable; instead the score lives in this
    wrapper whose only purpose is "I am a query hit."
    """

    score: float
    """Relevance score, always higher-is-better.  Backends whose
    metric is a distance (lower-is-better) negate it before returning,
    so the value is comparable within one store but not across stores
    using different metrics."""

    document_id: str
    """The ID of the source document the matched chunk belongs to —
    the same value carried by :attr:`VectorRecord.document_id` at
    insertion time.  Lets callers cite, group, or delete the source
    document of a hit."""

    chunk: Chunk
    """The matched business payload."""


class DocumentSummary(BaseModel):
    """A lightweight description of one source document inside a collection.

    Aggregated by :meth:`VectorStoreBase.list_documents` from the
    records of each ``document_id`` — the vector store is the single
    source of truth for "what documents exist in a knowledge base".
    """

    document_id: str
    """The source document identifier — the same value carried by
    :attr:`VectorRecord.document_id` at insertion time."""

    source: str
    """The original filename, taken from the first chunk encountered.
    All chunks of the same document share the same filename so any
    chunk yields the same value."""

    chunk_count: int
    """The total number of chunks indexed for this document."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Document-level metadata propagated from the parser / uploader
    (media type, size, upload time, ...).  Taken from the first chunk
    encountered."""


_ReadHits = Callable[[], Awaitable[list[VectorSearchResult]]]
_ReadWindow = Callable[
    [str, int, dict | None, set[str]],
    Awaitable[list[VectorSearchResult]],
]
_PublishWindow = Callable[
    [int, _ReadWindow],
    Awaitable[list[VectorSearchResult]],
]
_RankStages = Callable[[_PublishWindow], Awaitable[list[VectorSearchResult]]]
_PublishPoints = Callable[
    [_RankStages, bool],
    Awaitable[list[VectorSearchResult]],
]
_SEARCH_FIELDS = {"collection", "query_vector", "top_k", "metadata_filter"}


def _forwarded_search_signature(
    backend: Signature,
    public: Signature,
    positional_count: int,
) -> Signature:
    """Expose public window arguments carried by a store's variadics."""
    receiver, *fields = backend.parameters.values()
    public_receiver, *standard = public.parameters.values()
    if receiver.kind == Parameter.VAR_POSITIONAL:
        fields.insert(0, receiver)
        receiver = public_receiver.replace(kind=Parameter.POSITIONAL_ONLY)

    declared = backend.parameters
    accepts_keywords = any(p.kind == Parameter.VAR_KEYWORD for p in fields)
    effective = [receiver]
    for field in fields:
        if field.kind == Parameter.VAR_POSITIONAL:
            # Only consume positional slots following the public prefix.
            prefix = effective[1:]
            if [p.name for p in prefix] == [
                p.name for p in standard[: len(prefix)]
            ]:
                for index, param in enumerate(
                    standard[len(prefix) :],
                    len(prefix),
                ):
                    if param.name in declared or (
                        accepts_keywords and index >= positional_count
                    ):
                        break
                    effective.append(param)
        elif field.kind == Parameter.VAR_KEYWORD:
            present = {p.name for p in effective}
            effective.extend(
                p.replace(kind=Parameter.KEYWORD_ONLY)
                for p in standard
                if p.name not in present
            )
        effective.append(field)
    return backend.replace(parameters=effective)


async def _eligible_window(
    read: Callable[[int], Awaitable[list[VectorSearchResult]]],
    top_k: int,
    excluded: set[str],
    max_limit: int | None,
    metadata_filter: dict | None = None,
    first_hits: list[VectorSearchResult] | None = None,
) -> tuple[list[VectorSearchResult], int]:
    """Grow the caller's ranked window until enough eligible hits remain."""
    limit = top_k
    while True:
        hits = first_hits if first_hits is not None else await read(limit)
        first_hits = None
        visible = [
            hit
            for hit in hits
            if hit.document_id not in excluded
            and (
                not metadata_filter
                or metadata_filter.items() <= hit.chunk.metadata.items()
            )
        ]
        if len(visible) >= top_k or len(hits) < limit or limit == max_limit:
            return visible[:top_k], len(hits)
        limit *= 2
        if max_limit is not None:
            limit = min(limit, max_limit)


@dataclass
class _StoreQuery:
    """A backend call with the arguments actually supplied by its caller."""

    store: "VectorStoreBase"
    implementation: Callable[..., Awaitable[list[VectorSearchResult]]]
    bound: BoundArguments
    read: _ReadHits
    forwards_filter: bool
    forwards_omitted_limit: bool

    @property
    def is_vector_search(self) -> bool:
        """Whether this backend call exposes the standard window parameters."""
        return _SEARCH_FIELDS <= self.bound.signature.parameters.keys()

    def argument(self, name: str) -> Any:
        """Resolve a window parameter without adding it to the replay."""
        return self.bound.arguments.get(
            name,
            self.bound.signature.parameters[name].default,
        )

    async def window(
        self,
        collection: str,
        limit: int,
        metadata_filter: dict | None,
        omit: set[str],
    ) -> list[VectorSearchResult]:
        """Replay the same query in a published slot with its exclusions."""
        call = self.bound.signature.bind(*self.bound.args, **self.bound.kwargs)
        call.arguments["collection"] = collection
        if self.forwards_omitted_limit:
            return await self._forwarded_window(
                call,
                collection,
                limit,
                metadata_filter,
                omit,
            )
        call.arguments["top_k"] = limit
        extra_filter = bool(metadata_filter) and (
            metadata_filter != self.argument("metadata_filter")
        )
        if self.forwards_filter and (extra_filter or omit):
            return await self._forwarded_window(
                call,
                collection,
                limit,
                metadata_filter,
                omit,
            )
        if extra_filter:
            call.arguments["metadata_filter"] = metadata_filter

        if not omit or "exclude_document_ids" in call.signature.parameters:
            if omit:
                call.arguments["exclude_document_ids"] = (
                    self.argument("exclude_document_ids") or set()
                ) | omit
            return await self.implementation(*call.args, **call.kwargs)
        receiver = next(iter(call.signature.parameters))
        if (
            call.signature.parameters.keys() == _SEARCH_FIELDS | {receiver}
            and "metadata_filter" in call.arguments
            and type(self.store).search_excluding
            is not VectorStoreBase.search_excluding
        ):
            return await self.store.search_excluding(
                collection,
                call.arguments["query_vector"],
                limit,
                call.arguments["metadata_filter"],
                exclude_document_ids=omit,
            )

        # Refill with the same backend-specific options on every read.
        async def read_window(size: int) -> list[VectorSearchResult]:
            call.arguments["top_k"] = size
            return await self.implementation(*call.args, **call.kwargs)

        hits, _ = await _eligible_window(
            read_window,
            limit,
            omit,
            # pylint: disable-next=protected-access
            self.store._max_search_limit,
        )
        return hits

    async def _forwarded_window(
        self,
        call: BoundArguments,
        collection: str,
        limit: int,
        scope: dict | None,
        omit: set[str],
    ) -> list[VectorSearchResult]:
        """Apply slot constraints after the forwarder resolves its defaults."""

        async def read_window(size: int | None) -> list[VectorSearchResult]:
            async def constrain(
                target: str,
                query: "_StoreQuery",
            ) -> list[VectorSearchResult]:
                if target != collection or not query.is_vector_search:
                    return await query.read()
                resolved = query.argument("metadata_filter") or {}
                merged = {**resolved, **(scope or {})}
                if not resolved.items() <= merged.items():
                    return []
                return await query.window(
                    target,
                    query.argument("top_k"),
                    merged or None,
                    omit,
                )

            if size is not None:
                call.arguments["top_k"] = size
            with _observe_store_searches((self.store, constrain)):
                return await self.implementation(*call.args, **call.kwargs)

        first_hits = None
        if self.forwards_omitted_limit:
            first_hits = await read_window(None)
            limit = len(first_hits)
        # A forwarder may also use its client directly. Enforce the same
        # constraints on its results and refill past ineligible hits.
        hits, _ = await _eligible_window(
            read_window,
            limit,
            omit,
            # pylint: disable-next=protected-access
            self.store._max_search_limit,
            scope,
            first_hits,
        )
        return hits


_ObserveSearch = Callable[
    [str, _StoreQuery],
    Awaitable[list[VectorSearchResult]],
]
_store_search_observer: ContextVar[
    tuple["VectorStoreBase | None", _ObserveSearch] | None
] = ContextVar("store_search_observer", default=None)


@contextmanager
def _observe_store_searches(
    binding: tuple["VectorStoreBase | None", _ObserveSearch] | None,
) -> Iterator[None]:
    """Observe only searches made within the current async read."""
    token = _store_search_observer.set(binding)
    try:
        yield
    finally:
        _store_search_observer.reset(token)


class VectorStoreBase(ABC):
    """Abstract base class for vector store backends.

    Subclasses implement the concrete connection and query logic for a
    specific vector database (Chroma, Milvus, Qdrant, FAISS, etc.).

    A single instance is shared across the entire application.  The
    underlying client SDK is expected to handle connection pooling and
    thread safety internally.
    """

    _max_search_limit: int | None = None
    """Largest result window the backend accepts for a single search."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Expose physical searches to the current hosted read, if any."""
        super().__init_subclass__(**kwargs)
        implementation = cls.__dict__.get("search")
        if not isfunction(implementation) or not iscoroutinefunction(
            implementation,
        ):
            return
        # Forwarders decorated with wraps still receive their actual *args
        # and **kwargs, even when they advertise the delegated signature.
        parameters = signature(implementation, follow_wrapped=False)
        public = signature(VectorStoreBase.search)
        if hasattr(implementation, "__wrapped__"):
            declared = signature(implementation).parameters
            if "top_k" in declared:
                public = public.replace(
                    parameters=[
                        p.replace(default=declared["top_k"].default)
                        if p.name == "top_k"
                        else p
                        for p in public.parameters.values()
                    ],
                )
        forwards_window = (
            not _SEARCH_FIELDS <= parameters.parameters.keys()
            and any(
                p.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)
                for p in parameters.parameters.values()
            )
        )

        @wraps(implementation)
        async def search(
            self: "VectorStoreBase",
            *args: Any,
            **options: Any,
        ) -> list[VectorSearchResult]:
            observer = _store_search_observer.get()
            if observer is None or (
                observer[0] is not None and observer[0] is not self
            ):
                return await implementation(self, *args, **options)
            collection = args[0] if args else options.get("collection")
            if collection is None:
                return await implementation(self, *args, **options)

            # A store may delegate to its parent or another physical search.
            # Observe the result of the outer call, including its own ranking.
            with _observe_store_searches(None):
                bound = parameters.bind(self, *args, **options)
                if forwards_window:
                    # A backend can also accept a non-vector overload.
                    # Keep its native binding if it lacks the public inputs.
                    try:
                        binding = _forwarded_search_signature(
                            parameters,
                            public,
                            len(args),
                        )
                        bound = binding.bind(self, *args, **options)
                    except (TypeError, ValueError):
                        pass
                return await observer[1](
                    collection,
                    _StoreQuery(
                        self,
                        implementation,
                        bound,
                        partial(implementation, self, *args, **options),
                        "metadata_filter" not in parameters.parameters,
                        "top_k" not in parameters.parameters
                        and "top_k" not in bound.arguments,
                    ),
                )

        cls.search = search

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Enter the async context — open connections if needed.

        The default implementation is a no-op.  Subclasses that need
        explicit connection setup should override this.

        Returns:
            `VectorStoreBase`: ``self``.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context — close connections if needed.

        The default implementation is a no-op.
        """

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_collection(
        self,
        name: str,
        dimensions: int,
    ) -> None:
        """Create a new collection (vector index).

        If the collection already exists, implementations should raise
        or silently no-op depending on the backend's semantics.

        Args:
            name (`str`):
                The collection name. Typically, the knowledge base ID.
            dimensions (`int`):
                The fixed vector dimensionality for this collection.
                All vectors inserted later must have this many elements.
        """

    async def ensure_compatible_collection(
        self,
        name: str,
        source: str,
        dimensions: int,
    ) -> None:
        """Ensure a companion whose search scores compare with the source.

        Stores supporting per-collection metrics can inspect the source and
        validate an existing companion before their results are merged.
        """
        del source  # Stores with a fixed metric only need the dimension.
        if not await self.has_collection(name):
            await self.create_collection(name, dimensions)

    async def is_search_index_ready(self, collection: str) -> bool:
        """Check an existing collection without waiting for an index build."""
        del collection  # Most stores make new collections searchable at once.
        return True

    @abstractmethod
    async def delete_collection(self, name: str) -> None:
        """Delete a collection and all its data.

        Args:
            name (`str`):
                The collection name to delete.
        """

    @abstractmethod
    async def has_collection(self, name: str) -> bool:
        """Check whether a collection exists.

        Args:
            name (`str`):
                The collection name to check.

        Returns:
            `bool`: ``True`` if the collection exists.
        """

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection.

        Args:
            collection (`str`):
                The target collection name.
            records (`list[VectorRecord]`):
                The records to insert (each carrying a
                :class:`Chunk` and its embedding vector).
        """

    async def prepare_for_replacement(self, collection: str) -> None:
        """Make earlier writes visible to deletion in the inactive slot.

        The target collection may not exist on its first use. Stores whose
        deletions already see pending writes need no preparation.
        """

    async def refresh_search(self, collection: str) -> None:
        """Flush deferred search updates on backends with explicit refresh."""

    async def insert_for_publication(
        self,
        collection: str,
        document_id: str,
        records: list[VectorRecord],
        metadata_filter: dict[str, Any] | None,
    ) -> None:
        """Write one replacement before switching the published document.

        Backends with asynchronous search indexing can wait for the exact
        write attempt here. Other stores retain their usual insert behavior.
        """
        del document_id, metadata_filter
        if records:
            await self.insert(collection, records)

    @abstractmethod
    async def delete(
        self,
        collection: str,
        document_id: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> None:
        """Delete all records belonging to one source document.

        Identifies records by the :attr:`VectorRecord.document_id`
        field that backends persist at insertion time.  This matches
        the typical RAG workflow where a user uploads or removes a
        file as a unit.

        Args:
            collection (`str`):
                The target collection name.
            document_id (`str`):
                The source document ID whose records should be
                removed.
            metadata_filter (`dict[str, Any] | None`, optional):
                Restrict deletion to records whose chunk metadata matches
                every ``key == value`` pair in addition to the document ID.
                ``None`` or an empty dict deletes all records of the
                document. Backends must not ignore a non-empty filter.
        """

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @abstractmethod
    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Find the most similar records to a query vector.

        Args:
            collection (`str`):
                The collection to search.
            query_vector (`list[float]`):
                The query embedding vector.
            top_k (`int`, defaults to ``5``):
                Maximum number of results to return.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict the search to records whose
                ``chunk.metadata`` matches every ``key == value`` pair
                in this dict.  Backends translate this into a native
                payload filter.  Used for defense-in-depth
                cross-tenant scoping when an isolation strategy
                co-locates multiple knowledge bases inside the same
                collection.

        Returns:
            `list[VectorSearchResult]`:
                Results ordered by descending
                :attr:`VectorSearchResult.score`.  Implementations must
                normalize a distance metric into that higher-is-better
                score, e.g. by negating it.
        """

    async def search_excluding(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None,
        *,
        exclude_document_ids: set[str],
    ) -> list[VectorSearchResult]:
        """Find the top eligible hits with legacy vector-store interfaces."""
        visible, _ = await self._search_excluding_window(
            collection,
            query_vector,
            top_k,
            metadata_filter,
            exclude_document_ids,
        )
        return visible

    async def _search_excluding_window(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None,
        exclude_document_ids: set[str],
    ) -> tuple[list[VectorSearchResult], int]:
        """Return eligible hits and the final unfiltered result count."""

        async def read(limit: int) -> list[VectorSearchResult]:
            return await self.search(
                collection,
                query_vector,
                limit,
                metadata_filter,
            )

        return await _eligible_window(
            read,
            top_k,
            exclude_document_ids,
            self._max_search_limit,
        )

    # ------------------------------------------------------------------
    # Document listing
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List all distinct source documents indexed in a collection.

        Aggregates records by :attr:`VectorRecord.document_id` and
        returns one :class:`DocumentSummary` per document.  Backends
        are free to use whatever scrolling / aggregation primitive
        they expose; this method is expected to be O(documents) not
        O(chunks) on backends that support payload-only scans.

        Args:
            collection (`str`):
                The target collection name.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict aggregation to records whose
                ``chunk.metadata`` matches every ``key == value`` pair
                in this dict.  Used together with the search-time
                filter when an isolation strategy co-locates multiple
                knowledge bases inside the same collection.

        Returns:
            `list[DocumentSummary]`:
                One summary per distinct ``document_id``, in
                unspecified order.
        """

    # ------------------------------------------------------------------
    # Chunk listing
    # ------------------------------------------------------------------

    async def list_chunks(
        self,
        collection: str,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """List the chunks of one source document in ``chunk_index`` order.

        Returns the slice ``[offset, offset + limit)`` of the document's
        chunks, ordered by :attr:`Chunk.chunk_index` ascending.  Because
        ``chunk_index`` is a dense, immutable ``0..N-1`` sequence within
        a document, the slice is equivalent to the payload filter
        ``offset <= chunk_index < offset + limit`` — backends should
        prefer that filter over sort-and-skip so a page never requires
        scanning the whole document.

        Deliberately **not** an ``@abstractmethod``: it was added after
        third-party subclasses of :class:`VectorStoreBase` already
        existed, and making it abstract would break them on upgrade.
        Backends that do not override it raise
        :class:`NotImplementedError`, which the application layer maps
        to HTTP 501 so clients can hide the chunk-browsing UI.

        Args:
            collection (`str`):
                The target collection name.
            document_id (`str`):
                The source document whose chunks should be listed —
                the same value carried by
                :attr:`VectorRecord.document_id` at insertion time.
            offset (`int`, defaults to ``0``):
                Number of leading chunks to skip; equals the first
                returned chunk's ``chunk_index``.
            limit (`int`, defaults to ``30``):
                Maximum number of chunks to return.
            metadata_filter (`dict[str, Any] | None`, optional):
                If provided, restrict listing to records whose
                ``chunk.metadata`` matches every ``key == value`` pair
                in this dict.  Used for defense-in-depth cross-tenant
                scoping when an isolation strategy co-locates multiple
                knowledge bases inside the same collection.

        Returns:
            `list[Chunk]`:
                At most ``limit`` chunks ordered by ``chunk_index``
                ascending; empty when the document has no records or
                ``offset`` is past the end.

        Raises:
            `NotImplementedError`:
                If the backend does not support chunk listing.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement list_chunks().",
        )


def _reject_none_metadata_values(
    store: str,
    metadata_filter: dict[str, Any] | None,
) -> None:
    """Raise for ``None`` values in a metadata filter.

    Only some backends can tell an explicitly null metadata field from a
    missing key. Stores that cannot call this so that a ``None`` value
    fails clearly instead of raising a backend error or silently matching
    records that lack the key, which would widen a scoping filter.

    Args:
        store (`str`):
            The store class name, used in the error message.
        metadata_filter (`dict[str, Any] | None`):
            The flat ``{key: value}`` filter to check.

    Raises:
        `ValueError`:
            If any filter value is ``None``.
    """
    null_keys = [
        key for key, value in (metadata_filter or {}).items() if value is None
    ]
    if null_keys:
        raise ValueError(
            f"{store} does not support None values in metadata_filter "
            f"(keys: {', '.join(map(repr, null_keys))}): the backend cannot "
            "tell an explicitly null field from a missing key.",
        )
