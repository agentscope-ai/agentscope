# -*- coding: utf-8 -*-
"""The xAI credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase


class XAICredential(CredentialBase):
    """The xAI credential model."""

    model_config = ConfigDict(
        title="xAI API",
    )

    type: Literal["xai_credential"] = "xai_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The xAI API key.",
    )
    """The xAI API key."""

    api_host: str = Field(
        default="api.x.ai",
        title="API Host",
        description=(
            "The xAI API host (without scheme). Override to point at a "
            "compatible/self-hosted endpoint."
        ),
    )
    """The xAI API host."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the XAIChatModel class."""
        from ..model import XAIChatModel

        return XAIChatModel
