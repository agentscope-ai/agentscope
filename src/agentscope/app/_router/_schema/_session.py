# -*- coding: utf-8 -*-
"""Request / response schemas for the session router."""
from pydantic import BaseModel, Field

from ....permission import PermissionContext, PermissionMode
from ....state import TaskContext
from ...storage._model._base import _RecordBase
from ...storage import (
    ASRModelConfig,
    ChatModelConfig,
    RealtimeModelConfig,
    SessionConfig,
    SessionKnowledgeConfig,
    SessionOrigin,
    SessionRecord,
    TTSModelConfig,
    TeamRecord,
    UserOrigin,
)
from ..._service import AgentView, SessionStatus


class TeamMemberView(BaseModel):
    """One row in :attr:`TeamDetailResponse.members`.

    Pairs each member's :class:`AgentView` with its single
    ``session_id`` so the UI can subscribe to the worker's chat
    stream without a separate lookup.
    """

    agent: AgentView = Field(
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
    leader_agent: AgentView | None = Field(
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
    asr_model_config: ASRModelConfig | None = Field(
        default=None,
        description="ASR model configuration. Can be set later via PATCH.",
    )
    realtime_model_config: RealtimeModelConfig | None = Field(
        default=None,
        description=(
            "Realtime voice model configuration. Can be set later via PATCH."
        ),
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
    asr_model_config: ASRModelConfig | None = Field(
        default=None,
        description="New ASR model configuration. "
        "Pass null to clear; omit to leave unchanged.",
    )
    realtime_model_config: RealtimeModelConfig | None = Field(
        default=None,
        description="New realtime voice model configuration. "
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
    read_only: bool | None = Field(
        default=None,
        description="Restrict the session to read-only operations.",
    )
    allow_workspace_edits: bool | None = Field(
        default=None,
        description="Grant edits within authorized working directories.",
    )
    interactive: bool | None = Field(
        default=None,
        description="Allow unresolved approvals to ask the user.",
    )
    cwd: str | None = Field(
        default=None,
        description=(
            "New working directory — absolute, or relative to the "
            "workspace root, and not confined to it. Pass null to "
            "reset to the root; omit to leave unchanged."
        ),
    )


class SessionListState(BaseModel):
    """The two runtime fields used to initialize the sidebar panels."""

    permission_context: PermissionContext
    tasks_context: TaskContext


class ListedSession(_RecordBase):
    """Session metadata and the sidebar projection of runtime state."""

    user_id: str
    agent_id: str
    origin: SessionOrigin = Field(default_factory=UserOrigin)
    team_id: str | None = None
    config: SessionConfig
    state: SessionListState

    @classmethod
    def from_record(cls, record: SessionRecord) -> "ListedSession":
        """Expose only effective permissions and tasks from runtime state."""
        return cls(
            **record.model_dump(exclude={"state"}),
            state=SessionListState(
                permission_context=record.state.effective_permission_context,
                tasks_context=record.state.tasks_context,
            ),
        )


class SessionView(BaseModel):
    """Sidebar session metadata, effective permissions, tasks and status."""

    session: ListedSession
    is_running: bool = Field(
        description=(
            "**Deprecated** — use :attr:`status`. True only while a "
            "worker holds the run lease, which a session parked on a "
            "confirmation prompt does not: this reads ``False`` for a "
            "session that is visibly waiting on the user."
        ),
    )
    status: SessionStatus = Field(
        description=(
            "The session's unified status: ``running``, ``idle``, "
            "``awaiting_permission`` or ``awaiting_external_result``. "
            "Exactly one applies at any moment, so a single indicator "
            "renders it without reconciling flags."
        ),
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
    event_cursor: str | None = Field(
        default=None,
        description=(
            "Latest replay event already included in these messages."
        ),
    )
    is_running: bool = Field(
        description="Whether the session is currently running.",
    )
    has_more: bool = Field(
        description=(
            "Whether there are older messages before this page. "
            "When ``True``, pass a message ID from this response as "
            "the ``before`` parameter to load the previous page."
        ),
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
