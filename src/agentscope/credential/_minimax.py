# -*- coding: utf-8 -*-
"""The MiniMax credential."""

from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

# MiniMax officially recommends its Anthropic-compatible endpoint for the
# M-series chat models, see
# https://platform.minimax.io/docs/api-reference/text-anthropic-api
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
            "The base URL for the MiniMax Anthropic-compatible API. "
            "Defaults to ``https://api.minimax.io/anthropic`` per "
            "MiniMax's official recommendation."
        ),
    )
    """The base URL for the MiniMax API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the MiniMaxChatModel class."""
        from ..model import MiniMaxChatModel

        return MiniMaxChatModel
