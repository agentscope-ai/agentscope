# -*- coding: utf-8 -*-
"""Interactive credential acquisition for a channel type.

Most platforms let an operator hand their credentials over out of band —
scanning a QR code, approving a consent screen — instead of pasting an
app secret into a form. What they have in common is a session: it opens,
the operator acts on another device, and it ends with the same
:class:`~agentscope.app.channel.ChannelBase.Credentials` the form would
have produced.

The service drives that session one step at a time and keeps every step's
state in the message bus, so any replica can serve any request. A
provider therefore holds **no** state of its own: whatever it needs
between steps it returns as ``provider_state``, and receives back on the
next call.
"""
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class BindingState(str, Enum):
    """Where a binding session is in its lifecycle."""

    PENDING = "pending"
    """Opened, waiting for the operator to act."""

    AUTHORIZED = "authorized"
    """Credentials obtained, waiting to be claimed."""

    FAILED = "failed"
    """The platform refused or the session expired."""

    CANCELLED = "cancelled"
    """Abandoned by the operator."""

    @property
    def is_terminal(self) -> bool:
        """Whether no further step can move this session."""
        return self in (
            BindingState.AUTHORIZED,
            BindingState.FAILED,
            BindingState.CANCELLED,
        )


class BindingStep(BaseModel):
    """What one call to a provider produced."""

    state: BindingState = BindingState.PENDING
    """The session's state after this step."""

    verification_url: str = ""
    """Where the operator must go to approve. Rendered as a QR code by
    the frontend; set on the opening step and unchanged afterwards."""

    credentials: dict[str, Any] = Field(default_factory=dict)
    """The platform's credentials, once :attr:`state` is
    ``AUTHORIZED``."""

    error: str = ""
    """Why the session failed, for the operator to read."""

    provider_state: dict[str, Any] = Field(default_factory=dict)
    """Whatever the provider needs on the next step (a device code, a
    PKCE verifier, the domain a tenant was redirected to). Opaque to
    everything but the provider that produced it."""

    retry_after_secs: int = 5
    """How long to wait before stepping again — the platform's polling
    interval. Enforced service-side, so a client cannot poll upstream
    faster than the platform allows."""

    expires_in_secs: int = 600
    """How long the session stays usable."""


class CredentialBindingBase:
    """Base for a channel type's interactive credential flow.

    Subclasses are instantiated once per process and must stay
    stateless: two consecutive steps of one session routinely land on
    different replicas.
    """

    async def begin(self) -> BindingStep:
        """Open a session and return what the operator must visit.

        Returns:
            `BindingStep`:
                ``PENDING`` with :attr:`~BindingStep.verification_url`
                and the ``provider_state`` to hand back to
                :meth:`advance`.
        """
        raise NotImplementedError

    async def advance(self, provider_state: dict[str, Any]) -> BindingStep:
        """Ask the platform once whether the operator has approved.

        Called on the client's status poll rather than from a loop of
        our own, so nothing outlives the request that triggered it.

        Args:
            provider_state (`dict[str, Any]`):
                What the previous step returned.

        Returns:
            `BindingStep`:
                Still ``PENDING``, or a terminal state.
        """
        raise NotImplementedError
