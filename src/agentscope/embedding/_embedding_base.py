# -*- coding: utf-8 -*-
"""The embedding model base class."""
from typing import Any, List, TYPE_CHECKING

from ._embedding_response import EmbeddingResponse

if TYPE_CHECKING:
    from ..model import ChatResponse
else:
    ChatResponse = "ChatResponse"


class EmbeddingModelBase:
    """Base class for embedding models."""

    model_name: str
    """The embedding model name"""

    def __init__(
        self,
        model_name: str,
    ) -> None:
        """Initialize the embedding model base class.

        Args:
            model_name (`str`):
                The name of the embedding model.
        """
        self.model_name = model_name

    async def __call__(
        self,
        text: List[str],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the embedding API with the given arguments."""
        raise NotImplementedError(
            f"The {self.__class__.__name__} class does not implement "
            f"the __call__ method.",
        )
