# -*- coding: utf-8 -*-
"""The DashScope credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr, WebsocketUrl

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase
    from ..realtime import RealtimeModelBase
    from ..tts import TTSModelBase

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

    realtime_base_url: WebsocketUrl | None = Field(
        default=None,
        title="Realtime API Base URL",
        description=(
            "Optional DashScope-compatible realtime WebSocket endpoint. "
            "Required when the HTTP base_url is customized."
        ),
    )

    def resolve_realtime_base_url(self) -> str | None:
        """Resolve the DashScope-compatible realtime WebSocket endpoint."""
        if self.realtime_base_url is not None:
            return str(self.realtime_base_url)
        if self.base_url.rstrip("/") == _DASHSCOPE_BASE_URL.rstrip("/"):
            return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        return None

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the DashScopeChatModel class."""
        from ..model import DashScopeChatModel

        return DashScopeChatModel

    @classmethod
    def get_realtime_model_class(cls) -> Type["RealtimeModelBase"]:
        """Return the DashScopeRealtimeModel class."""
        from ..realtime import DashScopeRealtimeModel

        return DashScopeRealtimeModel

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
    def get_embedding_model_class(cls) -> Type["EmbeddingModelBase"]:
        """Return the DashScopeEmbeddingModel class."""
        from ..embedding import DashScopeEmbeddingModel

        return DashScopeEmbeddingModel
