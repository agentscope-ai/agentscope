# -*- coding: utf-8 -*-
"""Abstract knowledge base manager.

The manager is the **lifecycle owner** of knowledge bases:

- it creates / lists / deletes :class:`KnowledgeBaseRecord` rows in
  storage,
- it allocates / drops the matching vector store collections,
- it resolves an embedding model from the record's credential and
  hands a ready-to-use :class:`KnowledgeBase` runtime back to callers.

Different subclasses encode different *isolation strategies*: one
collection per knowledge base, a single shared collection scoped by
metadata, native VDB namespaces, etc.  All of them share the same
:class:`KnowledgeBaseManagerBase` interface so the rest of the
application can stay strategy-agnostic.

The manager is created once at application startup and stored on
``app.state.knowledge_base_manager``.  See
:func:`~agentscope.app.create_app` for the wiring.
"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Self

from ._dimension_policy import DimensionPolicy

if TYPE_CHECKING:
    from types import TracebackType

    from ...storage import (
        EmbeddingModelConfig,
        KnowledgeBaseRecord,
        StorageBase,
    )
    from ....rag import KnowledgeBase, VectorStoreBase


class KnowledgeBaseManagerBase(ABC):
    """Abstract base for knowledge base managers.

    Subclasses implement a specific isolation strategy by overriding
    :meth:`create_knowledge_base`, :meth:`delete_knowledge_base`, and
    :meth:`get_knowledge`.  The bookkeeping methods
    (:meth:`get_knowledge_base`, :meth:`list_knowledge_bases`) have
    default implementations that delegate to the bound storage.
    """

    def __init__(
        self,
        storage: "StorageBase",
        vector_store: "VectorStoreBase",
    ) -> None:
        """Initialize the manager.

        Args:
            storage (`StorageBase`):
                The application-wide storage backend used to persist
                :class:`KnowledgeBaseRecord` rows and resolve
                credentials.
            vector_store (`VectorStoreBase`):
                The application-wide vector store instance shared by
                every knowledge base allocated by this manager.
        """
        self._storage = storage
        self._vector_store = vector_store

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Enter the manager's lifetime.

        Enters the bound vector store's async context so a single
        ``create_app`` parameter (the manager) covers the vector store's
        lifecycle too. Subclasses that override this MUST call
        ``await super().__aenter__()`` first to keep the vector store
        ready before subclass-specific setup runs.
        """
        await self._vector_store.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: "TracebackType | None",
    ) -> None:
        """Exit the manager's lifetime, releasing the vector store.

        Args:
            exc_type (`type[BaseException] | None`):
                The exception type raised inside the with-block, if any.
            exc (`BaseException | None`):
                The exception instance raised inside the with-block,
                if any.
            tb (`TracebackType | None`):
                The traceback for the raised exception, if any.
        """
        await self._vector_store.__aexit__(exc_type, exc, tb)

    # ------------------------------------------------------------------
    # Capability discovery
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_dimension_policy(self) -> DimensionPolicy:
        """Return the embedding-dimension policy this manager enforces.

        Surfaced over HTTP so the front-end can soft-filter
        incompatible models / dimensions before submission and show a
        helpful banner.

        Returns:
            `DimensionPolicy`:
                The current dimension policy.
        """

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_knowledge_base(
        self,
        user_id: str,
        name: str,
        description: str,
        embedding_model_config: "EmbeddingModelConfig",
    ) -> "KnowledgeBaseRecord":
        """Create a new knowledge base for the given user.

        Implementations must:

        1. validate ``embedding_model_config.dimensions`` against
           :meth:`get_dimension_policy`;
        2. allocate the vector store collection (or namespace) the
           strategy uses;
        3. persist a :class:`KnowledgeBaseRecord` and return it.

        Args:
            user_id (`str`):
                The owner user id.
            name (`str`):
                Display name.
            description (`str`):
                Free-form description.
            embedding_model_config (`EmbeddingModelConfig`):
                Embedding model configuration; pinned to the record.

        Returns:
            `KnowledgeBaseRecord`:
                The newly persisted record.

        Raises:
            `DimensionPolicyError`:
                If the requested dimension violates the manager's
                dimension policy.
        """

    async def get_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> "KnowledgeBaseRecord | None":
        """Fetch a knowledge base record by id (delegates to storage).

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The knowledge base id.

        Returns:
            `KnowledgeBaseRecord | None`:
                The record, or ``None`` if not found / not owned by
                the user.
        """
        return await self._storage.get_knowledge_base(
            user_id,
            knowledge_base_id,
        )

    async def list_knowledge_bases(
        self,
        user_id: str,
    ) -> "list[KnowledgeBaseRecord]":
        """List all knowledge base records owned by the given user.

        Args:
            user_id (`str`):
                The owner user id.

        Returns:
            `list[KnowledgeBaseRecord]`:
                All knowledge base records belonging to the user.
        """
        return await self._storage.list_knowledge_bases(user_id)

    async def update_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> "KnowledgeBaseRecord | None":
        """Update mutable fields on an existing knowledge base record.

        Only ``name`` and ``description`` are mutable.  The embedding
        model configuration and the underlying collection are pinned
        for the lifetime of the record because changing either would
        invalidate every previously inserted vector.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The knowledge base id.
            name (`str | None`, optional):
                New display name; ``None`` leaves the name unchanged.
            description (`str | None`, optional):
                New description; ``None`` leaves the description
                unchanged.

        Returns:
            `KnowledgeBaseRecord | None`:
                The updated record, or ``None`` if the record was not
                found / not owned by the user.
        """
        record = await self._storage.get_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        if record is None:
            return None
        if name is not None:
            record.name = name
        if description is not None:
            record.description = description
        return await self._storage.upsert_knowledge_base(user_id, record)

    @abstractmethod
    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> bool:
        """Delete a knowledge base record and its underlying storage.

        Implementations must:

        1. authorise the call by looking the record up first;
        2. drop the vector store collection (or scope) the strategy
           uses;
        3. remove the :class:`KnowledgeBaseRecord` from storage.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The id of the knowledge base to delete.

        Returns:
            `bool`:
                ``True`` if the record existed and was deleted,
                ``False`` if it was not found.
        """

    # ------------------------------------------------------------------
    # KnowledgeBase runtime
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_knowledge(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> "KnowledgeBase":
        """Resolve a runtime :class:`KnowledgeBase` handle for one KB.

        Implementations are responsible for:

        - looking the record up in storage (authorisation);
        - resolving the embedding model from the record's credential;
        - constructing the :class:`KnowledgeBase` with the strategy's
          collection name and metadata filter.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The knowledge base id.

        Returns:
            `KnowledgeBase`:
                A runtime handle bound to this knowledge base.

        Raises:
            `KnowledgeBaseNotFoundError`:
                If the record does not exist or does not belong to
                the authenticated user.
        """
