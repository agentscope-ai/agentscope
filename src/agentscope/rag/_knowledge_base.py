# -*- coding: utf-8 -*-
"""The knowledge base abstraction for retrieval-augmented generation (RAG)."""
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .._utils._common import _get_timestamp
from ..embedding import EmbeddingModelBase
from ..message import VideoBlock, AudioBlock, ImageBlock, TextBlock
from ._store import EmbeddingStoreBase
from ..types import Embedding


@dataclass
class RetrievalResponse:
    """The retrieval response."""

    content: TextBlock | ImageBlock | AudioBlock | VideoBlock
    """The retrieved data."""

    embedding: Embedding
    """The embedding of the retrieved data."""

    score: float | None = field(default_factory=lambda: None)
    """The relevance scores of the retrieved data."""

    created_at: str = field(default_factory=_get_timestamp)
    """The timestamp of the retrieval response creation."""

    id: str = field(default_factory=lambda: _get_timestamp(True))


class KnowledgeBase:
    """The knowledge base abstraction for retrieval-augmented generation
    (RAG).

    .. note:: Only the `retrieve` and `add_text` methods are required to be
     implemented. Other methods are optional and can be overridden as needed.

    This class provides multimodal data support, including text, image,
    audio, and video. Specific implementations can choose to support one or
    more of these data types.
    """

    embedding_store: EmbeddingStoreBase
    """The embedding store for the knowledge base."""

    embedding_model: EmbeddingModelBase
    """The embedding model for the knowledge base."""

    def __init__(
        self,
        embedding_store: EmbeddingStoreBase,
        embedding_model: EmbeddingModelBase,
    ) -> None:
        """Initialize the knowledge base."""
        self.embedding_store = embedding_store
        self.embedding_model = embedding_model

    @abstractmethod
    async def retrieve(
        self,
        queries: list[str],
        **kwargs: Any
    ) -> RetrievalResponse:
        """Retrieve relevant data for the given queries."""

    @abstractmethod
    async def add_text(self, text: list[str], **kwargs: Any) -> None:
        """Add a text document to the knowledge base."""

    async def add_file(self, filepath: str, **kwargs: Any) -> None:
        """Add a file to the knowledge base. Optional to implement."""
        raise NotImplementedError(
            f"The {self.__class__.__name__} class does not implement "
            "the add_file method."
        )

    async def add_image(self, image: ImageBlock, **kwargs: Any) -> None:
        """Add an image to the knowledge base. Optional to implement."""
        raise NotImplementedError(
            f"The {self.__class__.__name__} class does not implement "
            "the add_image method."
        )

    async def add_audio(self, audio: AudioBlock, **kwargs: Any) -> None:
        """Add an audio file to the knowledge base. Optional to implement."""
        raise NotImplementedError(
            f"The {self.__class__.__name__} class does not implement "
            "the add_audio method."
        )

    async def add_video(self, video: VideoBlock, **kwargs: Any) -> None:
        """Add a video file to the knowledge base. Optional to implement."""
        raise NotImplementedError(
            f"The {self.__class__.__name__} class does not implement "
            "the add_video method."
        )
