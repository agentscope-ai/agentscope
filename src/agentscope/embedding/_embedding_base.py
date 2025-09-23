# -*- coding: utf-8 -*-
"""The embedding model base class."""
from typing import Any

from ._embedding_response import EmbeddingResponse


class EmbeddingModelBase:
    """Base class for embedding models."""

    model_name: str
    """The embedding model name"""

    supported_modalities: list[str]
    """The supported data modalities, e.g. "text", "image", "video"."""

    dimensions: int
    """The dimensions of the embedding vector."""

    def __init__(
        self,
        model_name: str,
        dimensions: int,
    ) -> None:
        """Initialize the embedding model base class.

        Args:
            model_name (`str`):
                The name of the embedding model.
            dimensions (`int`):
                The dimension of the embedding vector.
        """
        self.model_name = model_name
        self.dimensions = dimensions

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the embedding API with the given arguments."""
        raise NotImplementedError(
            f"The {self.__class__.__name__} class does not implement "
            f"the __call__ method.",
        )
