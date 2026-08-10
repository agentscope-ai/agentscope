# -*- coding: utf-8 -*-
"""The ELLM credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase


class EllmCredential(CredentialBase):
    """The credential for an ELLM (Enterprise Large Language Model) gateway.

    Unlike most OpenAI-compatible providers, ``base_url`` is **required**:
    there is no sensible default that would point at the public
    ``api.openai.com``, and silently falling back to it would route
    requests to the wrong endpoint.

    Only the generic ELLM fields live on this core class.  Provider-specific
    runtime fields (key-service endpoint, scene code, key expiry, default
    model name) belong on a downstream subclass in the consumer package —
    see e.g. ``BocomEllmCredential`` in the BOCOM adapter.
    """

    model_config = ConfigDict(
        title="ELLM API",
    )

    type: Literal["ellm_credential"] = "ellm_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The ELLM API key.",
    )
    """The API key."""

    base_url: str = Field(
        description=(
            "The base URL for the ELLM gateway, e.g. "
            "http://eaip-chn-slb-7006.bocomm.com/ELLM.ELLM-ADAPTER.V-1.0/v1"
        ),
    )
    """The base URL for the ELLM gateway (required)."""

    organization: str | None = Field(
        default=None,
        description="The organization ID, if any.",
    )
    """The organization ID, if any."""

    inject_think_tag: bool = Field(
        default=False,
        description=(
            "Whether to inject a ``<think>`` tag in front of the first "
            "non-empty text delta of streaming responses."
        ),
    )
    """Whether to inject a ``<think>`` tag in streaming responses."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the EllmChatModel class."""
        from ..model import EllmChatModel

        return EllmChatModel
