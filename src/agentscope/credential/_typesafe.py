# -*- coding: utf-8 -*-
"""The TypeSafe AI credential."""
from typing import Literal, TYPE_CHECKING, Type

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase


class TypeSafeCredential(CredentialBase):
    """The credential used by TypeSafe System One models."""

    model_config = ConfigDict(title="TypeSafe AI API")

    type: Literal["typesafe_credential"] = "typesafe_credential"
    """The credential type discriminator."""

    api_key: SecretStr = Field(description="The TypeSafe API key.")
    """The TypeSafe API key."""

    base_url: str | None = Field(
        default=None,
        description="An optional custom TypeSafe API base URL.",
    )
    """An optional custom TypeSafe API base URL."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """TypeSafe credentials serve classifier models only."""
        raise NotImplementedError(f"{cls.__name__} has no chat model.")
