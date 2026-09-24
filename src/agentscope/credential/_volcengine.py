# -*- coding: utf-8 -*-
"""The Volcengine credential."""

from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_VOLCENGINE_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


class VolcengineCredential(CredentialBase):
    """The Volcengine credential model."""

    model_config = ConfigDict(
        title="Volcengine Ark API",
    )

    type: Literal["volcengine_credential"] = "volcengine_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Volcengine Ark API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_VOLCENGINE_BASE_URL,
        description="The base URL for the Volcengine Ark API.",
    )
    """The base URL for the Volcengine API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the VolcengineChatModel class."""
        from ..model import VolcengineChatModel

        return VolcengineChatModel
