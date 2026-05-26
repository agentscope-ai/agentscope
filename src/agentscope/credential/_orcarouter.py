# -*- coding: utf-8 -*-
"""The OrcaRouter credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_ORCAROUTER_BASE_URL = "https://api.orcarouter.ai/v1"


class OrcaRouterCredential(CredentialBase):
    """The OrcaRouter credential model.

    OrcaRouter (https://www.orcarouter.ai) is an OpenAI-compatible meta-router
    that exposes 150+ models from upstream providers (OpenAI, Anthropic,
    Google, DeepSeek, Qwen, xAI, MiniMax, etc.) behind a single API key and
    endpoint, plus an adaptive routing model id ``orcarouter/auto`` that
    selects the best upstream per request.
    """

    model_config = ConfigDict(
        title="OrcaRouter API",
    )

    type: Literal["orcarouter_credential"] = "orcarouter_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The OrcaRouter API key (prefix ``sk-orca-``).",
    )
    """The API key."""

    base_url: str = Field(
        default=_ORCAROUTER_BASE_URL,
        description="The base URL for the OrcaRouter API.",
    )
    """The base URL for the OrcaRouter API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the OrcaRouterChatModel class."""
        from ..model import OrcaRouterChatModel

        return OrcaRouterChatModel
