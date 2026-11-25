# -*- coding: utf-8 -*-
"""Physical addresses for the hosted knowledge base's alternate vectors."""
from ....rag import KnowledgeBase


def version_collection(collection: str) -> str:
    """Return the companion collection for a managed vector collection."""
    return f"{collection}_versions"


class _VersionKnowledge(KnowledgeBase):
    """Bind an alternate collection to the primary collection's schema."""

    def __init__(self, knowledge: KnowledgeBase) -> None:
        super().__init__(
            knowledge.name,
            knowledge.description,
            knowledge.embedding_model,
            knowledge.vector_store,
            version_collection(knowledge.collection),
            knowledge.metadata_filter,
        )
        self._source = knowledge

    async def ensure_collection(self) -> None:
        """Validate existing collections too, including after recreation."""
        await self._source.ensure_collection()
        async with self._collection_lock:
            await self.vector_store.ensure_compatible_collection(
                self.collection,
                self._source.collection,
                self.embedding_model.dimensions,
            )


def version_knowledge(knowledge: KnowledgeBase) -> KnowledgeBase:
    """Bind an alternate slot with the primary metric and tenant filter."""
    return _VersionKnowledge(knowledge)
