# -*- coding: utf-8 -*-
"""The Moonshot AI credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"


class MoonshotCredential(CredentialBase):
    """The Moonshot AI credential model."""

    model_config = ConfigDict(
        title="Moonshot API",
    )

    type: Literal["moonshot_credential"] = "moonshot_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Moonshot AI API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_MOONSHOT_BASE_URL,
        description="The base URL for the Moonshot AI API.",
    )
    """The base URL for the Moonshot AI API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the MoonshotChatModel class."""
        from ..model import MoonshotChatModel

        return MoonshotChatModel

    async def list_remote_models(self) -> list[str] | None:
        """Query the endpoint's OpenAI-compatible ``GET /models`` listing."""
        from ._base import _list_openai_compatible_models

        return await _list_openai_compatible_models(
            self.base_url,
            self.api_key.get_secret_value(),
        )
