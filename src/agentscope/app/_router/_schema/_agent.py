# -*- coding: utf-8 -*-
"""Request / response schemas for the agent router."""
import warnings

from pydantic import BaseModel, Field

from ....agent import ContextConfig, ReActConfig
from ...storage import ChatConfig, InviteConfig
from ..._service import AgentView


class CreateAgentRequest(BaseModel):
    """Request body for creating a new agent.

    Mirrors :class:`AgentData`, so the shape a client reads back is the
    shape it writes. The three pre-``chat_config`` fields below are
    still accepted and are folded into it by
    :class:`AgentData`; sending both puts the flat one on top.
    """

    name: str = Field(description="Display name of the agent.")
    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="Base system prompt fed to the agent.",
    )
    chat_config: ChatConfig = Field(
        default_factory=ChatConfig,
        description="Settings for the agent's text conversations.",
    )
    context_config: ContextConfig | None = Field(
        default=None,
        description="**Deprecated.** Use ``chat_config.context_config``.",
    )
    react_config: ReActConfig | None = Field(
        default=None,
        description="**Deprecated.** Use ``chat_config.react_config``.",
    )
    invite_config: InviteConfig | None = Field(
        default=None,
        description="**Deprecated.** Use ``chat_config.invite_config``.",
    )


class CreateAgentResponse(BaseModel):
    """Response body after creating an agent."""

    agent_id: str = Field(description="Server-assigned agent identifier.")


class UpdateAgentRequest(BaseModel):
    """Request body for partially updating an agent.

    Omit any field to keep its current value.
    """

    name: str | None = Field(default=None, description="New display name.")
    system_prompt: str | None = Field(
        default=None,
        description="New system prompt.",
    )
    chat_config: ChatConfig | None = Field(
        default=None,
        description=(
            "New text-conversation settings. Replaces the block as a "
            "whole, so pass every sub-config that should survive."
        ),
    )
    context_config: ContextConfig | None = Field(
        default=None,
        description=(
            "**Deprecated.** Use ``chat_config``. Still honoured, and "
            "narrower: it replaces only this sub-config."
        ),
    )
    react_config: ReActConfig | None = Field(
        default=None,
        description="**Deprecated.** Use ``chat_config``.",
    )
    invite_config: InviteConfig | None = Field(
        default=None,
        description="**Deprecated.** Use ``chat_config``.",
    )


class ListAgentsResponse(BaseModel):
    """Response body for listing agents."""

    agents: list[AgentView] = Field(description="Agent records.")
    total: int = Field(description="Total number of agents.")


class AgentSchemaResponse(BaseModel):
    """**Deprecated.** JSON Schema fragments used by the frontend to
    render the agent create / edit forms.

    Superseded by :class:`AgentSchemaV2Response`, which returns the full
    :class:`AgentData` JSON Schema in a single ``schema`` field so newly
    added agent fields (like the ``invite_config`` sub-model) reach the
    frontend automatically without the router having to know about them.

    The frontend previously split :class:`AgentData` into three
    hand-picked sections (``identity``, ``context_config``,
    ``react_config``) here, which required a router edit every time a
    new user-editable field landed on :class:`AgentData`. Kept for
    backwards compatibility with pre-v2 API consumers.
    """

    identity: dict = Field(
        description=(
            "Schema for the agent's identity fields (``name``, "
            "``system_prompt``)."
        ),
    )
    context_config: dict = Field(
        description="Schema for ``ContextConfig``.",
    )
    react_config: dict = Field(
        description="Schema for ``ReActConfig``.",
    )


# The ``schema`` field name below is intentional — the wire contract for
# ``GET /agent/schema/v2`` is ``{"schema": ...}`` so the response is
# self-documenting. Pydantic v2's :meth:`BaseModel.schema` is a
# deprecated legacy classmethod (superseded by ``model_json_schema``);
# a like-named instance field triggers a cosmetic "shadows an attribute
# in parent BaseModel" warning that is irrelevant here because we never
# call the legacy classmethod. Suppress it locally instead of adding an
# alias that would obscure the wire contract at every call site.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "schema" in "AgentSchemaV2Response"',
    )

    class AgentSchemaV2Response(BaseModel):
        """Response for ``GET /agent/schema/v2``.

        Wraps the full :class:`AgentData` JSON Schema in a single
        ``schema`` field so the frontend can render every user-editable
        property without the router having to enumerate them.
        """

        schema: dict = Field(
            description=(
                "Full :class:`AgentData` JSON Schema. All user-editable "
                "fields appear as top-level entries in ``properties`` — "
                "the frontend derives its section grouping from this "
                "single schema."
            ),
        )
