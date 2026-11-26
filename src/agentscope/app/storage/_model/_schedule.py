# -*- coding: utf-8 -*-
"""The schedule storage model."""
from copy import deepcopy
from datetime import datetime
from enum import Enum

from pydantic import Field

from ._base import _RecordBase
from ._session import ChatModelConfig
from ._permission import AccessSettings
from ....permission import PermissionMode, PermissionRule
from ....state import AgentState
from ....state._state import ScheduleSessionLimits
from ....permission._evidence import (
    AuthorizationEvidence,
    ScheduleAuthorization,
)


class ScheduleSource(str, Enum):
    """The source that created the schedule.

    Attributes:
        USER: Created manually by the user via the UI.
        AGENT: Created automatically by an agent, e.g. via a tool call.
    """

    USER = "USER"
    AGENT = "AGENT"


def _get_local_timezone() -> str:
    """Get the local timezone.

    Returns:
        `str`:
            The local timezone.
    """
    try:
        from tzlocal import get_localzone

        return str(get_localzone())
    except Exception:
        return "UTC"


class ScheduleData(AccessSettings):
    """The schedule configuration data."""

    name: str = Field(description="Display name of the schedule.")

    description: str = Field(
        default="",
        description="The description of the schedule, including its purpose, "
        "trigger conditions, etc.",
    )

    enabled: bool = Field(
        default=True,
        description="Whether the schedule is active. Disabled schedules are "
        "retained but will not trigger.",
    )

    timezone: str = Field(
        default=_get_local_timezone(),
        description="IANA timezone name used to evaluate the cron expression, "
        "e.g. 'America/New_York' or 'Asia/Shanghai'.",
    )

    cron_expression: str = Field(
        description="Standard 5-field cron expression, e.g. '0 9 * * 1-5'.",
    )

    started_at: datetime = Field(
        description="The date and time the schedule was started.",
        default_factory=lambda: datetime.now().astimezone(),
    )

    ended_at: datetime | None = Field(
        default=None,
        description="The date and time the schedule was ended.",
    )

    chat_model_config: ChatModelConfig = Field(
        description="Model configuration for the auto-created session.",
    )

    stateful: bool = Field(
        title="Stateful",
        default=False,
        description="Whether consecutive executions share the same session "
        "context. If not, each execution will have its own state.",
    )

    permission_mode: PermissionMode = Field(
        title="Permission mode",
        default=PermissionMode.AUTO,
        description="Approval mode for scheduled execution. Automatic "
        "review is used by default; unresolved approvals are denied.",
    )

    deny_rules: dict[str, list[PermissionRule]] = Field(default_factory=dict)
    """Hard prohibitions inherited from the creating session."""

    authorized_description: str | None = None
    """Exact task description supplied through the authenticated user API."""
    authorization_evidence: list[AuthorizationEvidence] = Field(
        default_factory=list,
    )
    """Original instructions supporting an agent-created schedule."""
    authorization_evidence_complete: bool = True
    """Whether the creating session retained all earlier instructions."""

    source: ScheduleSource = Field(
        default=ScheduleSource.USER,
        description="Indicates how this schedule was created.",
    )

    source_session_id: str = Field(
        default="",
        description="The source session identifier, used for resource "
        "retrieval.",
    )


class ScheduleRecord(_RecordBase):
    """Persisted schedule record."""

    user_id: str = Field(description="Owner user id.")

    agent_id: str = Field(
        description="The agent id that will execute the schedule.",
    )

    data: ScheduleData = Field(description="Schedule configuration.")

    def apply_to_session(
        self,
        state: AgentState,
        *,
        new_session: bool = False,
    ) -> None:
        """Refresh schedule policy while respecting explicit session limits."""
        context = state.permission_context
        limits = state.schedule_session_limits
        if limits is None:
            # Older records have no provenance for the last policy values.
            limits = (
                ScheduleSessionLimits()
                if new_session
                else ScheduleSessionLimits.from_context(context)
            )
            state.schedule_session_limits = limits
        context.mode = self.data.permission_mode.restricted_by(limits.mode)
        context.read_only = self.data.read_only or limits.read_only
        context.allow_workspace_edits = (
            self.data.allow_workspace_edits and limits.allow_workspace_edits
        )
        context.interactive = False
        for name, rules in self.data.deny_rules.items():
            current = context.deny_rules.setdefault(name, [])
            current.extend(deepcopy(r) for r in rules if r not in current)
        state.schedule_authorization = self.review_authorization()

    def review_authorization(self) -> ScheduleAuthorization:
        """Bind original authorization to the current task description."""
        return ScheduleAuthorization(
            schedule_id=self.id,
            user_id=self.user_id,
            source=self.data.source.value,
            description=self.data.description,
            authorized_description=self.data.authorized_description
            if self.data.authorized_description == self.data.description
            else None,
            evidence=self.data.authorization_evidence,
            complete=self.data.authorization_evidence_complete,
        )
