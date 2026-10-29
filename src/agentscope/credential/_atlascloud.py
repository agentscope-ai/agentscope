# -*- coding: utf-8 -*-
"""The Atlas Cloud credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_ATLASCLOUD_BASE_URL = "https://api.atlascloud.ai/v1"


class AtlasCloudCredential(CredentialBase):
    """The Atlas Cloud credential model."""

    model_config = ConfigDict(
        title="Atlas Cloud API",
    )

    type: Literal["atlascloud_credential"] = "atlascloud_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The Atlas Cloud API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_ATLASCLOUD_BASE_URL,
        description="The Atlas Cloud OpenAI-compatible API base URL.",
    )
    """The Atlas Cloud OpenAI-compatible API base URL."""

    organization: str | None = Field(
        default=None,
        exclude=True,
        description="Unused compatibility field for OpenAI-compatible clients.",
    )
    """Compatibility field consumed by the OpenAI chat client."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the AtlasCloudChatModel class."""
        from ..model import AtlasCloudChatModel

        return AtlasCloudChatModel
