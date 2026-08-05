# -*- coding: utf-8 -*-
"""The Google Gemini credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr, WebsocketUrl

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase
    from ..realtime import RealtimeModelBase
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

    realtime_base_url: WebsocketUrl | None = Field(
        default=None,
        title="Realtime API Base URL",
        description=(
            "Optional Gemini Live-compatible WebSocket endpoint. The "
            "official Gemini Live endpoint is used when omitted."
        ),
    )

    def resolve_realtime_base_url(self) -> str | None:
        """Resolve the Gemini Live WebSocket endpoint."""
        if self.realtime_base_url is not None:
            return str(self.realtime_base_url)
        return (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService."
            "BidiGenerateContent"
        )

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
    def get_realtime_model_class(cls) -> Type["RealtimeModelBase"]:
        """Return the GeminiRealtimeModel class."""
        from ..realtime import GeminiRealtimeModel

        return GeminiRealtimeModel

    @classmethod
    def get_tts_model_classes(cls) -> list[Type["TTSModelBase"]]:
        """Return the Gemini TTS model classes."""
        from ..tts import GeminiTTSModel

        return [GeminiTTSModel]
