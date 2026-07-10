# -*- coding: utf-8 -*-
"""The OpenAI credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase
    from ..tts import TTSModelBase


class OpenAICredential(CredentialBase):
    """The OpenAI credential model."""

    model_config = ConfigDict(
        title="OpenAI API",
    )

    type: Literal["openai_credential"] = "openai_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The OpenAI API key.",
    )
    """The API key."""

    organization: str | None = Field(
        default=None,
        description="The OpenAI organization ID.",
    )
    """The OpenAI organization ID."""

    base_url: str | None = Field(
        default=None,
        description=(
            "The base URL for the OpenAI API. "
            "Can be used for OpenAI-compatible endpoints."
        ),
    )
    """Custom base URL for OpenAI-compatible endpoints."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the OpenAIChatModel class."""
        from ..model import OpenAIChatModel

        return OpenAIChatModel

    @classmethod
    def get_embedding_model_class(cls) -> Type["EmbeddingModelBase"]:
        """Return the OpenAIEmbeddingModel class."""
        from ..embedding import OpenAIEmbeddingModel

        return OpenAIEmbeddingModel

    @classmethod
    def get_tts_model_classes(cls) -> list[Type["TTSModelBase"]]:
        """Return the OpenAI TTS model classes."""
        from ..tts import OpenAITTSModel

        return [OpenAITTSModel]
