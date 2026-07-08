# -*- coding: utf-8 -*-
"""Request / response schemas for the session router."""
from pydantic import BaseModel, Field

from ....permission import PermissionMode
from ...storage import (
    AgentRecord,
    ChatModelConfig,
    SessionKnowledgeConfig,
    TTSModelConfig,
    SessionRecord,
    TeamRecord,
)
from ..._service import SessionStatus


class TeamMemberView(BaseModel):
    """One row in :attr:`TeamDetailResponse.members`.

    Pairs each member's :class:`AgentRecord` with its single
    ``session_id`` so the UI can subscribe to the worker's chat
    stream without a separate lookup.
    """

    agent: AgentRecord = Field(
        description="The worker agent record.",
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "The worker's session id. ``None`` if the agent is in an "
            "inconsistent state (worker without a session)."
        ),
    )


class TeamDetailResponse(BaseModel):
    """Resolved team detail embedded inside :class:`SessionView.team`."""

    team: TeamRecord = Field(description="The team record.")
    leader_agent: AgentRecord | None = Field(
        default=None,
        description=(
            "Leader's agent record (resolved from the team's "
            "``session_id`` → session.agent_id)."
        ),
    )
    members: list[TeamMemberView] = Field(
        default_factory=list,
        description=(
            "Worker agents listed in :attr:`TeamData.member_ids`, each "
            "paired with its single session id when available."
        ),
    )


class CreateSessionRequest(BaseModel):
    """Request body for creating a new session."""

    agent_id: str = Field(description="Agent this session belongs to.")
    workspace_id: str | None = Field(
        default=None,
        description=(
            "Optional explicit workspace binding. When omitted the "
            "server calls "
            "``WorkspaceManagerBase.assign_workspace_id`` under the "
            "configured isolation policy. Set only to force a "
            "specific binding (e.g. share workspace with another "
            "existing session)."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Display name. Defaults to current datetime if omitted.",
    )
    chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="Model provider and parameters. "
        "Can be set later via PATCH.",
    )
    fallback_chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="Fallback model used when the primary model fails. "
        "Can be set later via PATCH.",
    )
    tts_model_config: TTSModelConfig | None = Field(
        default=None,
        description="TTS model configuration. Can be set later via PATCH.",
    )
    knowledge_config: SessionKnowledgeConfig | None = Field(
        default=None,
        description=(
            "Knowledge bases attached to this session plus the "
            "`RAGMiddleware` parameters. Can be set later "
            "via PATCH."
        ),
    )


class CreateSessionResponse(BaseModel):
    """Response body after creating a session."""

    session_id: str = Field(description="Server-assigned session identifier.")


class UpdateSessionRequest(BaseModel):
    """Request body for updating an existing session.

    Omit any field to keep its current value.
    """

    name: str | None = Field(
        default=None,
        description="New display name.",
    )
    chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="New model configuration. "
        "Replaces the existing one entirely. "
        "Pass null to clear; omit to leave unchanged.",
    )
    fallback_chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="New fallback model configuration. "
        "Pass null to clear; omit to leave unchanged.",
    )
    tts_model_config: TTSModelConfig | None = Field(
        default=None,
        description="New TTS model configuration. "
        "Pass null to clear; omit to leave unchanged.",
    )
    knowledge_config: SessionKnowledgeConfig | None = Field(
        default=None,
        description=(
            "New knowledge base attachment + middleware parameters. "
            "Pass null to clear; omit to leave unchanged."
        ),
    )
    permission_mode: PermissionMode | None = Field(
        default=None,
        description="New permission mode for the session.",
    )


class SessionView(BaseModel):
    """Per-session bundle with everything the frontend needs to
    render either the list view or open a session.

    Bundles three orthogonal pieces of information so opening a
    session does not require a waterfall of follow-up requests:

    - the persisted :class:`SessionRecord` itself (config + state),
    - whether the session has an active chat run right now,
    - the team detail (resolved leader + members) when the session
      participates in a team.

    Messages are intentionally **not** included here — they are
    paginated separately via ``GET /sessions/{id}/messages``.
    """

    session: SessionRecord = Field(
        description=(
            "The persisted session record. Includes ``state`` "
            "(``permission_context`` / ``tool_context`` / "
            "``tasks_context``) inline."
        ),
    )
    is_running: bool = Field(
        description="Whether a chat run is currently active on this session.",
    )
    team: TeamDetailResponse | None = Field(
        default=None,
        description=(
            "Resolved team detail when ``session.team_id`` is set "
            "(leader agent + member agents with their session ids). "
            "``None`` when the session does not participate in any team."
        ),
    )


class ListSessionsResponse(BaseModel):
    """Response body for listing sessions."""

    sessions: list[SessionView] = Field(
        description="Session views (record + is_running + team).",
    )
    total: int = Field(description="Total number of sessions.")


class ListMessagesResponse(BaseModel):
    """Response body for listing messages in a session."""

    messages: list = Field(description="Messages in chronological order.")
    is_running: bool = Field(
        description="Whether the session is currently running.",
    )


class SessionStatusResponse(BaseModel):
    """Response body for probing a session's high-level status.

    See :class:`~agentscope.app._service.SessionStatus` for the
    semantics of each ``status`` value and the precedence rules used
    to derive it.
    """

    session_id: str = Field(description="The session that was probed.")
    status: SessionStatus = Field(
        description=(
            "The session's unified status. One of ``running`` "
            "(some worker holds the run lease), ``idle`` (no worker, "
            "context clean), ``awaiting_permission`` (no worker, "
            "context parked on HITL tool call), or "
            "``awaiting_external_result`` (no worker, context parked "
            "on external executor)."
        ),
    )


class InterruptSessionResponse(BaseModel):
    """Response body for ``POST /sessions/{sid}/interrupt`` (HTTP 202).

    The interrupt operation is idempotent and always succeeds for an
    existing session (only ``404`` is raised when the session id does
    not exist):

    - If the session is **running**, an interrupt signal is published
      so the local
      :class:`~agentscope.app._manager.CancelDispatcher` cancels the
      chat-run task; the agent then runs its ``CancelledError`` cleanup
      path.
    - If the session is **parked** on HITL / external execution, a
      resume trigger carrying a
      :class:`~agentscope.event.UserInterruptEvent` is enqueued so the
      agent short-circuits into the same cleanup path.
    - If the session is **idle**, the call is a no-op.
    """

    session_id: str = Field(description="Echo of the interrupted session id.")
