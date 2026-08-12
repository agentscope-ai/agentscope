# -*- coding: utf-8 -*-
"""The MiniMax credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..tts import TTSModelBase


class MiniMaxCredential(CredentialBase):
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

    @classmethod
    def get_tts_model_classes(cls) -> list[Type["TTSModelBase"]]:
        """Return the MiniMax TTS model classes."""
        from ..tts import MiniMaxTTSModel

        return [MiniMaxTTSModel]
