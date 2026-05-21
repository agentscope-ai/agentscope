# -*- coding: utf-8 -*-
"""The Moonshot AI credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"


class MoonshotCredential(CredentialBase):
    """The Moonshot AI credential model."""

    type: Literal["moonshot_credential"] = "moonshot_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Moonshot AI API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_MOONSHOT_BASE_URL,
        description="The base URL for the Moonshot AI API.",
    )
    """The base URL for the Moonshot AI API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the MoonshotChatModel class."""
        from ..model import MoonshotChatModel

        return MoonshotChatModel
