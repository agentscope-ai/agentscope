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
        title="Anthropic API",
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

    async def list_remote_models(self) -> list[str] | None:
        """Query the Anthropic ``GET /v1/models`` listing."""
        from ._base import _http_get_json

        payload = await _http_get_json(
            f"{(self.base_url or 'https://api.anthropic.com').rstrip('/')}"
            "/v1/models",
            headers={
                "x-api-key": self.api_key.get_secret_value(),
                "anthropic-version": "2023-06-01",
            },
        )
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        return sorted(
            {
                entry["id"]
                for entry in entries
                if isinstance(entry, dict) and "id" in entry
            },
        )
