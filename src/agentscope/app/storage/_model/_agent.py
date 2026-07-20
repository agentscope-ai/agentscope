# -*- coding: utf-8 -*-
"""The agent storage class."""
from typing import Literal, Self

from pydantic import Field, BaseModel, model_validator
from pydantic.json_schema import SkipJsonSchema

from ...._utils._common import _generate_id
from ._base import _RecordBase
from ....agent import ContextConfig, ReActConfig


class InviteConfig(BaseModel):
    """User-editable invite settings for :class:`AgentData`.

    Kept in its own sub-model so the frontend's schema-driven form —
    which renders any nested-object property as its own fieldset —
    picks it up as a dedicated section without a per-field allowlist.
    Also keeps the cross-field ``invitable ⇒ non-empty description``
    invariant local to this model.
    """

    invitable: bool = Field(
        default=False,
        description=(
            "Whether this agent may be borrowed into another agent's team "
            "via the ``AgentInvite`` tool. Independent from "
            ":attr:`invite_description` so the user can preserve an "
            "authored blurb while temporarily disabling the toggle. "
            "``invitable=True`` requires a non-empty "
            ":attr:`invite_description` (enforced by validator)."
        ),
        title="Invitable",
    )

    invite_description: str | None = Field(
        default=None,
        description=(
            "Free-text blurb shown to a leader LLM in the ``AgentInvite`` "
            "tool description — used by the leader to decide whether to "
            "borrow this agent. Persisted across toggle off/on so the "
            "user's authored draft is not lost when :attr:`invitable` "
            "is temporarily disabled."
        ),
        title="Invite Description",
        json_schema_extra={"format": "textarea"},
    )

    @model_validator(mode="after")
    def _check_invitable_has_description(self) -> Self:
        """Reject ``invitable=True`` without a non-empty description.

        The blurb is what the leader LLM sees when it inspects the
        ``AgentInvite`` tool; without it, the LLM cannot make a sensible
        choice. Rejecting at the model boundary (rather than in the
        service layer) means PATCH / POST return HTTP 422 automatically.
        """
        if self.invitable and not (self.invite_description or "").strip():
            raise ValueError(
                "invite_description must be non-empty when invitable=True",
            )
        return self


class AgentData(BaseModel):
    """The agent data model."""

    id: SkipJsonSchema[str] = Field(
        description="Unique agent id",
        default_factory=_generate_id,
    )
    """The agent id.

    Server-assigned; never edited via the create / update form.
    Annotated with :class:`SkipJsonSchema` so it is dropped from
    ``AgentData.model_json_schema()`` (the frontend renders the form
    off that schema) while still being serialised in normal JSON
    dumps (so persisted records keep the id).
    """

    name: str = Field(
        description="The name of the agent.",
        title="Name",
    )

    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="The system prompt for the agent.",
        title="System Prompt",
        # Hint for schema-driven UI renderers; see ``ContextConfig`` for
        # the same pattern on long-form prompts.
        json_schema_extra={"format": "textarea"},
    )

    context_config: ContextConfig = Field(
        description="The context config for the agent.",
        title="Context Config",
    )

    react_config: ReActConfig = Field(
        description="The react config for the agent.",
        title="React Config",
    )

    invite_config: InviteConfig = Field(
        default_factory=InviteConfig,
        description="The invite config for the agent.",
        title="Invite Config",
    )


class AgentRecord(_RecordBase):
    """The agent ORM model."""

    user_id: str
    """The user id"""

    source: Literal["user", "team"] = "user"
    """How this agent was created.

    - ``"user"``: created directly by the user (default). Can have multiple
      sessions and is listed in the user's regular agent list.
    - ``"team"``: spawned as a team worker by another agent's
      ``create_team`` / ``team_add_member`` tool. Has exactly one session.
      Team membership itself is session-level and stored on
      :class:`SessionRecord.team_id`.
    """

    data: AgentData
    """The agent data"""
