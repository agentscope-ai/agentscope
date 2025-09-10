# -*- coding: utf-8 -*-
""""""
from typing import Any

from agentscope.embedding import EmbeddingModelBase
from agentscope.message import TextBlock, ImageBlock, AudioBlock, VideoBlock
from agentscope.rag import KnowledgeBase, EmbeddingStoreBase, VectorRecord


class Knowledge(KnowledgeBase):
    """The knowledge base implementation."""

    def __init__(
        self,
        embedding_store: EmbeddingStoreBase,
        embedding_model: EmbeddingModelBase,
    ) -> None:
        """Initialize the knowledge base."""
        super().__init__(embedding_store, embedding_model)

    async def retrieve(self, queries: list[str], **kwargs: Any) -> list[
        TextBlock | ImageBlock | AudioBlock | VideoBlock]:
        pass

    async def add_text(self, text: list[str], **kwargs: Any) -> None:
        """Add a text document to the knowledge base."""

        res_embeddings = await self.embedding_model(text)

        res = await self.embedding_store.add(
            [
                VectorRecord(
                    embedding=_,

                ) for _ in res_embeddings.embeddings
            ]
        )

        return res
