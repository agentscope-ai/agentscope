# -*- coding: utf-8 -*-
"""The MiniMax credential."""

from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

# Default to an OpenAI-compatible endpoint. Users can override via
# the ``base_url`` field if their deployment uses a different host.
MINIMAX_DEFAULT_BASE_URL = "https://api.minimax.cn/v1"


class MiniMaxCredential(CredentialBase):
    """The credential for the MiniMax API.

    MiniMax exposes an OpenAI-compatible ``/v1/chat/completions`` endpoint,
    so this credential follows the same shape as the other OpenAI-style
    providers in AgentScope.
    """

    model_config = ConfigDict(
        title="MiniMax API",
    )

    type: Literal["MiniMax_credential"] = "MiniMax_credential"
    """The type of the credential."""

    api_key: SecretStr = Field(
        description="The MiniMax API key.",
        title="API Key",
    )

    base_url: str = Field(
        default=MINIMAX_DEFAULT_BASE_URL,
        title="API Base URL",
        description="The MiniMax OpenAI-compatible API base URL.",
    )

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the MiniMaxChatModel class."""
        from ..model import MiniMaxChatModel

        return MiniMaxChatModel
