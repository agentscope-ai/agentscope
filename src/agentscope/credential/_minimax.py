# -*- coding: utf-8 -*-
"""The MiniMax credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ModelCard
    from ..tts import TTSModelBase


_MINIMAX_GLOBAL_BASE_URL = "https://api.minimax.io"


class MiniMaxCredential(CredentialBase):  # pylint: disable=abstract-method
    """The credential for MiniMax APIs."""

    model_config = ConfigDict(
        title="MiniMax API",
    )

    type: Literal["minimax_credential"] = "minimax_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The MiniMax API key.",
        title="API Key",
    )
    """The API key."""

    base_url: Literal[
        "https://api.minimax.io",
        "https://api.minimaxi.com",
    ] = Field(
        default=_MINIMAX_GLOBAL_BASE_URL,
        description="The regional base URL for the MiniMax API.",
    )
    """The regional base URL for the MiniMax API."""

    @classmethod
    def list_models(cls) -> list["ModelCard"]:
        """Return no chat models for this TTS-only credential."""
        return []

    @classmethod
    def get_tts_model_classes(cls) -> list[Type["TTSModelBase"]]:
        """Return the MiniMax TTS model classes."""
        from ..tts import MiniMaxTTSModel

        return [MiniMaxTTSModel]
