# -*- coding: utf-8 -*-
"""The knowledge base abstraction for retrieval-augmented generation (RAG)."""
from abc import abstractmethod
from typing import Any

from ._reader import Document
from ..embedding import EmbeddingModelBase
from ._store import VDBStoreBase


class KnowledgeBase:
    """The knowledge base abstraction for retrieval-augmented generation
    (RAG).

    .. note:: Only the `retrieve` and `add_text` methods are required to be
     implemented. Other methods are optional and can be overridden as needed.

    This class provides multimodal data support, including text, image,
    audio, and video. Specific implementations can choose to support one or
    more of these data types.
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
