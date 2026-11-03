# -*- coding: utf-8 -*-
"""The DashScope credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import (
    ConfigDict,
    Field,
    SecretStr,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase
    from ..realtime import RealtimeModelBase
    from ..tts import TTSModelBase, VoiceCloningClientBase

_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class DashScopeCredential(CredentialBase):
    """The credential for DashScope API."""

    model_config = ConfigDict(
        title="DashScope API",
    )

    type: Literal["dashscope_credential"] = "dashscope_credential"
    """The type of the credential."""

    api_key: SecretStr = Field(
        description="The DashScope API key.",
        title="API Key",
    )

    base_url: str = Field(
        default=_DASHSCOPE_BASE_URL,
        title="API Base URL",
        description=(
            "The base URL for the DashScope OpenAI-compatible API endpoint."
        ),
    )

    websocket_url: str | None = Field(
        default=None,
        pattern=r"^wss?://[^/?#]+(?:[/?#].*)?$",
        title="TTS WebSocket URL",
        description=(
            "Optional CosyVoice/Qwen Audio WebSocket endpoint for gateways. "
            "Overrides automatic resolution and DashScope SDK settings."
        ),
    )

    @model_serializer(mode="wrap")
    def _serialize_endpoint_choice(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict:
        """Keep an implicit endpoint implicit across storage round trips."""
        data = handler(self)
        if "base_url" not in self.model_fields_set:
            data.pop("base_url", None)
        return data

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the DashScopeChatModel class."""
        from ..model import DashScopeChatModel

        return DashScopeChatModel

    @classmethod
    def get_voice_cloning_client_class(
        cls,
    ) -> Type["VoiceCloningClientBase"] | None:
        """Return the voice creation client, or None when unsupported."""
        from ..tts import DashScopeVoiceCloningClient

        return DashScopeVoiceCloningClient

    @classmethod
    def get_tts_model_classes(cls) -> list[Type["TTSModelBase"]]:
        """Return the DashScope TTS model classes."""
        from ..tts import (
            DashScopeCosyVoiceTTSModel,
            DashScopeRealtimeTTSModel,
            DashScopeTTSModel,
        )

        return [
            DashScopeTTSModel,
            DashScopeRealtimeTTSModel,
            DashScopeCosyVoiceTTSModel,
        ]

    @classmethod
    def get_realtime_model_classes(
        cls,
    ) -> list[Type["RealtimeModelBase"]]:
        """Return the DashScope realtime model classes."""
        from ..realtime import (
            DashScopeAudioRealtimeModel,
            DashScopeRealtimeModel,
        )

        return [DashScopeRealtimeModel, DashScopeAudioRealtimeModel]

    @classmethod
    def get_embedding_model_class(cls) -> Type["EmbeddingModelBase"]:
        """Return the DashScopeEmbeddingModel class."""
        from ..embedding import DashScopeEmbeddingModel

        return DashScopeEmbeddingModel
