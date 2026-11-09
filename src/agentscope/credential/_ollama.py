# -*- coding: utf-8 -*-
"""The Ollama credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase


class OllamaCredential(CredentialBase):
    """The Ollama credential model (connection settings)."""

    model_config = ConfigDict(
        title="Ollama API",
    )

    type: Literal["ollama_credential"] = "ollama_credential"
    """The credential type."""

    host: str | None = Field(
        default=None,
        description=(
            "The Ollama server host URL. "
            "Defaults to http://localhost:11434 if not specified."
        ),
    )
    """The Ollama server host URL."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the OllamaChatModel class."""
        from ..model import OllamaChatModel

        return OllamaChatModel

    @classmethod
    def get_embedding_model_class(cls) -> Type["EmbeddingModelBase"]:
        """Return the OllamaEmbeddingModel class."""
        from ..embedding import OllamaEmbeddingModel

        return OllamaEmbeddingModel
