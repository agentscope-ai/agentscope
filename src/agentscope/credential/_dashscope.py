# -*- coding: utf-8 -*-
"""The DashScope credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase


class DashScopeCredential(CredentialBase):
    """The credential for DashScope API."""

    type: Literal["dashscope_credential"] = "dashscope_credential"
    """The type of the credential."""

    api_key: SecretStr = Field(
        description="The DashScope API key.",
        title="API Key",
    )

    base_http_api_url: str | None = Field(
        default=None,
        title="API Base URL",
        description="The base URL of the DashScope API.",
    )

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the DashScopeChatModel class."""
        from ..model import DashScopeChatModel

        return DashScopeChatModel
