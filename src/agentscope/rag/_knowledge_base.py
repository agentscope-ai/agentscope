# -*- coding: utf-8 -*-
"""The knowledge base abstraction for retrieval-augmented generation (RAG)."""
from abc import abstractmethod
from typing import Any

from ._reader import Document
from ..embedding import EmbeddingModelBase
from ._store import VDBStoreBase
from ..message import TextBlock
from ..tool import ToolResponse


class KnowledgeBase:
    """The knowledge base abstraction for retrieval-augmented generation
    (RAG).

    The ``retrieve`` and ``add_documents`` methods need to be implemented
    in the subclasses. We also provide a quick method ``retrieve_knowledge``
    that enables the agent to retrieve knowledge easily.
    """

    embedding_store: VDBStoreBase
    """The embedding store for the knowledge base."""

    embedding_model: EmbeddingModelBase
    """The embedding model for the knowledge base."""

    def __init__(
        self,
        embedding_store: VDBStoreBase,
        embedding_model: EmbeddingModelBase,
    ) -> None:
        """Initialize the knowledge base."""
        self.embedding_store = embedding_store
        self.embedding_model = embedding_model

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant documents by the given query.

        Args:
            query (`str`):
                The query string to retrieve relevant documents.
            limit (`int`, defaults to 5):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, defaults to `None`):
                The score threshold to filter the retrieved documents. If
                provided, only documents with a score higher than the
                threshold will be returned.
            **kwargs (`Any`):
                Other keyword arguments for the vector database search API.
        """

    @abstractmethod
    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to the knowledge base, which will embed the documents
        and store them in the embedding store.

        Args:
            documents (`list[Document]`):
                A list of documents to add.
        """

    # A quick method that enable the agent to retrieve knowledge
    # Developers can wrap the `retrieve` method by themselves to support
    # more flexible usage
    async def retrieve_knowledge(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> ToolResponse:
        """Retrieve relevant documents from the knowledge base. Note the
        `query` parameter is directly related to the retrieval quality, and
        for the same question, you can try many different queries to get the
        best results. Adjust the `limit` and `score_threshold` parameters
        to get more or fewer results.

        Args:
            query (`str`):
                The query string, which should be specific and concise. For
                example, you should provide the specific name instead of
                "you", "my", "he", "she", etc.
            limit (`int`, defaults to 3):
                The number of relevant documents to retrieve.
            score_threshold (`float`, defaults to 0.8):
                A threshold in [0, 1] and only the relevance score above this
                threshold will be returned. Reduce this value to get more
                results.
        """

        docs = await self.retrieve(
            query=query,
            limit=limit,
            score_threshold=score_threshold,
            **kwargs,
        )

        if len(docs):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Score: {_.score}, "
                        f"Content: {_.metadata.content['text']}",
                    )
                    for _ in docs
                ],
            )
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="No relevant documents found. TRY to reduce the "
                    "`score_threshold` parameter to get "
                    "more results.",
                ),
            ],
        )
