# -*- coding: utf-8 -*-
"""The AI/ML API credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_AIMLAPI_BASE_URL = "https://api.aimlapi.com/v1"


class AIMLAPICredential(CredentialBase):
    """The aimlapi.com credential model.

    aimlapi.com is an OpenAI-compatible aggregator: a single key and base URL
    reach several hundred chat models from different vendors. Only the
    ``/v1/chat/completions`` surface is used — ``/v1/completions`` does not
    exist there, and ``/v1/responses`` serves a small subset of the catalog.
    """

    model_config = ConfigDict(
        title="aimlapi.com",
    )

    type: Literal["aimlapi_credential"] = "aimlapi_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The aimlapi.com API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_AIMLAPI_BASE_URL,
        description="The base URL for the aimlapi.com API.",
    )
    """The base URL for the aimlapi.com API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the AIMLAPIChatModel class."""
        from ..model import AIMLAPIChatModel

        return AIMLAPIChatModel
