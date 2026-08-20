# -*- coding: utf-8 -*-
"""The Google Gemini credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase
    from ..tts import TTSModelBase


class GeminiCredential(CredentialBase):
    """The Google Gemini credential model."""

    model_config = ConfigDict(
        title="Gemini API",
    )

    type: Literal["gemini_credential"] = "gemini_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Google Gemini API key.",
    )
    """The API key."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the GeminiChatModel class."""
        from ..model import GeminiChatModel

        return GeminiChatModel

    @classmethod
    def get_embedding_model_class(cls) -> Type["EmbeddingModelBase"]:
        """Return the GeminiEmbeddingModel class."""
        from ..embedding import GeminiEmbeddingModel

        return GeminiEmbeddingModel

    @classmethod
    def get_tts_model_classes(cls) -> list[Type["TTSModelBase"]]:
        """Return the Gemini TTS model classes."""
        from ..tts import GeminiTTSModel

        return [GeminiTTSModel]
