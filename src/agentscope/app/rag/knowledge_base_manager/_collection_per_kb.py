# -*- coding: utf-8 -*-
"""Collection-per-knowledge-base isolation strategy.

The simplest correct implementation: every knowledge base gets its own
vector store collection sized to the chosen embedding model.  No
cross-KB co-location, no namespace gymnastics — collection names are
the isolation key.

Because each knowledge base owns its collection outright, the
dimension policy reported by this manager is always
:attr:`DimensionPolicyKind.ANY`: the user is free to pick any
dimension supported by their embedding model.

The collection name is generated as ``kb_<uuid_hex>`` (no user id is
encoded, since storage does the user-scoped authorisation).
"""
from typing import TYPE_CHECKING

from ._base import KnowledgeBaseManagerBase
from ._dimension_policy import DimensionPolicy, DimensionPolicyKind
from ._errors import KnowledgeBaseNotFoundError
from ...._logging import logger
from ....rag import KnowledgeBase
from ..._service._embedding import build_embedding_model
from ...storage import KnowledgeBaseRecord, KnowledgeBaseData

if TYPE_CHECKING:
    from ...storage import EmbeddingModelConfig


class CollectionPerKbManager(KnowledgeBaseManagerBase):
    """One-collection-per-KB knowledge base manager.

    Each knowledge base maps to one collection in the bound
    :class:`~agentscope.rag.VectorStoreBase`.  The collection is
    created at :meth:`create_knowledge_base` time and dropped at
    :meth:`delete_knowledge_base` time.
    """

    async def get_dimension_policy(self) -> DimensionPolicy:
        """Return :attr:`DimensionPolicyKind.ANY` — every KB picks freely.

        Returns:
            `DimensionPolicy`:
                The dimension policy ``(ANY, None)``.
        """
        return DimensionPolicy(
            kind=DimensionPolicyKind.ANY,
            dimension=None,
        )

    async def create_knowledge_base(
        self,
        user_id: str,
        name: str,
        description: str,
        embedding_model_config: "EmbeddingModelConfig",
    ) -> KnowledgeBaseRecord:
        """Allocate a new collection and persist the knowledge base record.

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
        """
        record = KnowledgeBaseRecord(
            user_id=user_id,
            data=KnowledgeBaseData(
                name=name,
                description=description,
                embedding_model_config=embedding_model_config,
                collection_name="",
            ),
        )
        record.data.collection_name = f"kb_{record.id}"

        await self._vector_store.create_collection(
            name=record.data.collection_name,
            dimensions=embedding_model_config.dimensions,
        )
        try:
            return await self._storage.upsert_knowledge_base(user_id, record)
        except Exception:
            # Compensating delete must not shadow the original exception:
            # if the delete itself raises, the caller would be misdirected
            # to investigate the wrong error and the orphan collection
            # would leave no trace.
            try:
                await self._vector_store.delete_collection(
                    record.data.collection_name,
                )
            except Exception:  # noqa: BLE001 — best-effort cleanup
                logger.exception(
                    "Failed to drop orphan collection %r after "
                    "upsert_knowledge_base failed; collection leaked.",
                    record.data.collection_name,
                )
            raise

    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> bool:
        """Drop the underlying collection and remove the record.

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
        record = await self._storage.get_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        if record is None:
            return False

        if await self._vector_store.has_collection(
            record.data.collection_name,
        ):
            await self._vector_store.delete_collection(
                record.data.collection_name,
            )
        return await self._storage.delete_knowledge_base(
            user_id,
            knowledge_base_id,
        )

    async def get_knowledge(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> KnowledgeBase:
        """Resolve a :class:`KnowledgeBase` runtime for one knowledge base.

        Looks the record up in storage (raising
        :class:`KnowledgeBaseNotFoundError` for unknown / un-owned
        ids), constructs the embedding model from the pinned
        credential, and returns a ready-to-use handle bound to the
        record's collection.

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
        record = await self._storage.get_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        if record is None:
            raise KnowledgeBaseNotFoundError(
                f"Knowledge base {knowledge_base_id!r} not found.",
            )

        # KB manager is an owner-internal path: the credential lives
        # under the same owner as the knowledge base, so we read it
        # directly from storage rather than routing through the access
        # service (which is only relevant when the viewer differs from
        # the owner).
        credential_record = await self._storage.get_credential(
            record.user_id,
            record.data.embedding_model_config.credential_id,
        )
        if credential_record is None:
            raise KnowledgeBaseNotFoundError(
                f"Credential "
                f"{record.data.embedding_model_config.credential_id!r} for "
                f"knowledge base {knowledge_base_id!r} not found.",
            )
        embedding_model = build_embedding_model(
            credential_record=credential_record,
            config=record.data.embedding_model_config,
        )
        return KnowledgeBase(
            name=record.data.name,
            description=record.data.description,
            embedding_model=embedding_model,
            vector_store=self._vector_store,
            collection=record.data.collection_name,
            metadata_filter=None,
        )
