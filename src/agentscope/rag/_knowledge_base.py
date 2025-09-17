# -*- coding: utf-8 -*-
"""The knowledge base abstraction for retrieval-augmented generation (RAG)."""
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ._reader import Document
from .._utils._common import _get_timestamp
from ..embedding import EmbeddingModelBase
from ..message import VideoBlock, AudioBlock, ImageBlock, TextBlock
from ._store import VDBStoreBase
from ..types import Embedding


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
        queries: list[str],
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant documents by the givne"""

    @abstractmethod
    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any
    ) -> None:
        """Add documents to the knowledge base, which will embed the documents
        and store them in the embedding store.

        Args:
            documents (`list[Document]`):
                A list of documents to add.
        """
