# -*- coding: utf-8 -*-
"""The Anthropic credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field, SecretStr, ConfigDict

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase


class AnthropicCredential(CredentialBase):
    """The Anthropic credential model."""

    model_config = ConfigDict(
        title="Anthropic Credential",
    )

    type: Literal["anthropic_credential"] = "anthropic_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Anthropic API key",
    )
    """The API key."""

    base_url: str | None = Field(
        description="The base URL for the Anthropic API.",
        default=None,
    )
    """The base URL for the Anthropic API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the AnthropicChatModel class."""
        from ..model import AnthropicChatModel

        return AnthropicChatModel
