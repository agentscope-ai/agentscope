# -*- coding: utf-8 -*-
"""The DashScope credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase
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

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the DashScopeChatModel class."""
        from ..model import DashScopeChatModel

        return DashScopeChatModel

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
