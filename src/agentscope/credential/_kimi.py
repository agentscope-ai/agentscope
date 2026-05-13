# -*- coding: utf-8 -*-
"""The Kimi (Moonshot AI) credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_KIMI_BASE_URL = "https://api.moonshot.cn/v1"


class KimiCredential(CredentialBase):
    """The Kimi (Moonshot AI) credential model."""

    type: Literal["kimi_credential"] = "kimi_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Kimi (Moonshot AI) API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_KIMI_BASE_URL,
        description="The base URL for the Kimi API.",
    )
    """The base URL for the Kimi API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the KimiChatModel class."""
        from ..model import KimiChatModel

        return KimiChatModel
