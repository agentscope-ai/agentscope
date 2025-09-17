# -*- coding: utf-8 -*-
"""A general implementation of the knowledge class in AgentScope RAG module."""
from typing import Any

from . import VectorRecord
from ._reader import Document
from ..embedding import EmbeddingModelBase
from ..message import (
    TextBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
)
from ._knowledge_base import (
    KnowledgeBase,
    VDBStoreBase,
)


class Knowledge(KnowledgeBase):
    """The knowledge base implementation."""

    def __init__(
        self,
        embedding_store: VDBStoreBase,
        embedding_model: EmbeddingModelBase,
    ) -> None:
        """Initialize the knowledge base."""
        super().__init__(embedding_store, embedding_model)

    async def retrieve(
        self, queries: list[str], **kwargs: Any
    ) -> list[TextBlock | ImageBlock | AudioBlock | VideoBlock]:
        """Retrieve relevant documents by the given queries."""

        queries_embedding = await self.embedding_model(
            [
                TextBlock(
                    type="text",
                    text=_,
                ) for _ in queries
            ]
        )

        self.embedding_store.retrieve()


    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to the knowledge

        Args:
            documents (`list[Document]`):
                The list of documents to add.
        """

        # Prepare the content to be embedded
        for doc in documents:
            if doc.content["type"] not in self.embedding_model.supported_modalities:
                raise ValueError(
                    f"The embedding model {self.embedding_model.model_name} "
                    f"does not support {doc.content['type']} data.",
                )

        # Get the embeddings
        res_embeddings = await self.embedding_model(
            [_.content for _ in documents]
        )

        res = await self.embedding_store.add(
            [
                VectorRecord(
                    embedding=embedding,
                    content=doc.content,
                    metadata={
                        "doc_id": doc.doc_id,
                        "chunk_id": doc.chunk_id,
                        "total_chunks": doc.total_chunks,
                    }
                )
                for embedding, doc in zip(
                    res_embeddings.embeddings,
                    documents,
                )
            ]
        )

        return res
