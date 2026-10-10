# -*- coding: utf-8 -*-
"""The DeepSeek credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekCredential(CredentialBase):
    """The DeepSeek credential model."""

    model_config = ConfigDict(
        title="DeepSeek API",
    )

    type: Literal["deepseek_credential"] = "deepseek_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The DeepSeek API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_DEEPSEEK_BASE_URL,
        description="The base URL for the DeepSeek API.",
    )
    """The base URL for the DeepSeek API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the DeepSeekChatModel class."""
        from ..model import DeepSeekChatModel

        return DeepSeekChatModel

    async def list_remote_models(self) -> list[str] | None:
        """Query the endpoint's OpenAI-compatible ``GET /models`` listing."""
        from ._base import _list_openai_compatible_models

        return await _list_openai_compatible_models(
            self.base_url,
            self.api_key.get_secret_value(),
        )
