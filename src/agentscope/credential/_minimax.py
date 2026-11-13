# -*- coding: utf-8 -*-
"""The MiniMax credential."""

from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase
    from ..tts import TTSModelBase, VoiceCloningClientBase

# MiniMax's recommended Anthropic-compatible endpoint (international site)
_MINIMAX_BASE_URL = "https://api.minimax.io/anthropic"


class MiniMaxCredential(CredentialBase):
    """The MiniMax credential model."""

    type: Literal["minimax_credential"] = "minimax_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The MiniMax API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_MINIMAX_BASE_URL,
        description=(
            "The base URL for the MiniMax Anthropic-compatible API, use "
            "``https://api.minimax.cn/anthropic`` for China mainland."
        ),
    )
    """The base URL for the MiniMax API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the MiniMaxChatModel class."""
        from ..model import MiniMaxChatModel

        return MiniMaxChatModel

    @classmethod
    def get_voice_cloning_client_class(
        cls,
    ) -> Type["VoiceCloningClientBase"] | None:
        """Return the MiniMax voice creation client."""
        from ..tts import MiniMaxVoiceCloningClient

        return MiniMaxVoiceCloningClient

    @classmethod
    def get_tts_model_classes(cls) -> list[Type["TTSModelBase"]]:
        """Return the MiniMax TTS model classes."""
        from ..tts import MiniMaxTTSModel

        return [MiniMaxTTSModel]
