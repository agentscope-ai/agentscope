# -*- coding: utf-8 -*-
"""The xAI credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase


class XAICredential(CredentialBase):
    """The xAI credential model."""

    type: Literal["xai_credential"] = "xai_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The xAI API key.",
    )
    """The xAI API key."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the XAIChatModel class."""
        from ..model import XAIChatModel

        return XAIChatModel
