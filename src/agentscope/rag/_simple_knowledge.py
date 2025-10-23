# -*- coding: utf-8 -*-
"""A general implementation of the knowledge class in AgentScope RAG module."""
from typing import Any

from ._reader import Document
from ..message import TextBlock
from ._knowledge_base import KnowledgeBase


class SimpleKnowledge(KnowledgeBase):
    """A simple knowledge base implementation."""

    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant documents by the given queries.

        Args:
            query (`str`):
                The query string to retrieve relevant documents.
            limit (`int`, defaults to 5):
                The number of relevant documents to retrieve.
            score_threshold: float | None = None,
                The threshold of the score to filter the results.
            **kwargs (`Any`):
                Other keyword arguments for the vector database search API.

        Returns:
            `list[Document]`:
                A list of relevant documents.

        TODO: handle the case when the query is too long.
        """
        res_embedding = await self.embedding_model(
            [
                TextBlock(
                    type="text",
                    text=query,
                ),
            ],
        )
        res = await self.embedding_store.search(
            res_embedding.embeddings[0],
            limit=limit,
            score_threshold=score_threshold,
            **kwargs,
        )
        return res

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
            if (
                doc.metadata.content["type"]
                not in self.embedding_model.supported_modalities
            ):
                raise ValueError(
                    f"The embedding model {self.embedding_model.model_name} "
                    f"does not support {doc.metadata.content['type']} data.",
                )

        # Get the embeddings
        res_embeddings = await self.embedding_model(
            [_.metadata.content for _ in documents],
        )

        for doc, embedding in zip(documents, res_embeddings.embeddings):
            doc.embedding = embedding

        await self.embedding_store.add(documents)
