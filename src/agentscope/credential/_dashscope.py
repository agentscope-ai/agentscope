# -*- coding: utf-8 -*-
"""The DashScope credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

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
