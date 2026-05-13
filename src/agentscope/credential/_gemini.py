# -*- coding: utf-8 -*-
"""The Google Gemini credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase


class GeminiCredential(CredentialBase):
    """The Google Gemini credential model."""

    type: Literal["gemini_credential"] = "gemini_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Google Gemini API key.",
    )
    """The API key."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the GeminiChatModel class."""
        from ..model import GeminiChatModel

        return GeminiChatModel
