# -*- coding: utf-8 -*-
"""BOCOM ELLM credential — the provider-specific extension of the core
:class:`agentscope.credential.EllmCredential`.

The core class carries only the generic ELLM fields (``api_key``,
``base_url``, ``inject_think_tag``).  The BOCOM adapter adds the
key-service runtime fields here so the core library stays free of
provider-specific concerns.

Persisted via ``_dump_with_secrets`` which dumps with ``model_dump`` —
only **declared** fields survive, so every field below must be an
explicit model field.
"""

from __future__ import annotations

from typing import Literal, Type, TYPE_CHECKING

from pydantic import Field

from agentscope.credential import EllmCredential

if TYPE_CHECKING:
    from agentscope.model import ChatModelBase


class BocomEllmCredential(EllmCredential):
    """BOCOM ELLM credential with key-service runtime fields.

    The discriminator ``type`` is distinct from the core
    ``ellm_credential`` so :class:`CredentialFactory` can route between
    the generic core class and this provider-specific subclass.
    """

    type: Literal["bocom_ellm_credential"] = "bocom_ellm_credential"
    """The credential type."""

    scene_code: str = Field(
        default="",
        description=(
            "The scene code for the ELLM key-service endpoint, e.g. "
            "P2024146."
        ),
    )
    """The scene code for the ELLM key-service endpoint."""

    api_key_url: str = Field(
        default="",
        description=(
            "The URL of the ELLM key-service endpoint used to obtain/refresh "
            "the API key, e.g. "
            "http://eaip-ellm-1.bocomm.com/ELLM.ELLM-OMSERVICE.V-1.0/"
            "createSceneApiKey.do"
        ),
    )
    """The URL of the ELLM key-service endpoint."""

    model: str = Field(
        default="",
        description="The default ELLM model name, e.g. Qwen3-235B-A22B.",
    )
    """The default ELLM model name."""

    apikey_expires_at: float | None = Field(
        default=None,
        description=(
            "The Unix timestamp (seconds) when the current API key expires. "
            "Managed by AutoRefreshEllmChatModel on key refresh; external "
            "updates may clear it (along with api_key) to force a refresh."
        ),
    )
    """The Unix timestamp (seconds) when the current API key expires."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the core :class:`EllmChatModel`.

        Key rotation is wired at runtime by the ``EllmKeyRefreshMiddleware``
        (which swaps the core model for the auto-refreshing subclass), so
        the credential still resolves to the plain core model class.
        """
        return EllmCredential.get_chat_model_class()


__all__ = ["BocomEllmCredential"]
