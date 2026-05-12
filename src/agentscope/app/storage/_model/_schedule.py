# -*- coding: utf-8 -*-
"""The schedule storage model."""
from pydantic import BaseModel, Field

from ._base import _RecordBase
from ._session import ChatModelConfig
from ....permission._types import PermissionMode


class ScheduleData(BaseModel):
    """The schedule data."""

    name: str = Field(description="Display name of the schedule.")

    description: str = Field(
        default="",
        description="Optional description of the schedule.",
    )

    cron_expression: str = Field(
        description="Standard 5-field cron expression, e.g. '0 9 * * 1-5'.",
    )

    agent_id: str = Field(description="Agent to run when the schedule fires.")

    workspace_id: str = Field(
        description="Workspace used when creating the triggered session.",
    )

    chat_model_config: ChatModelConfig = Field(
        description="Model configuration for the auto-created session.",
    )

    input: dict | None = Field(
        default=None,
        description="Serialised Msg sent to the agent on each trigger. "
        "None means the agent is invoked with no user input.",
    )

    permission_mode: PermissionMode = Field(
        default=PermissionMode.DONT_ASK,
        description="Permission level for the agent during scheduled execution. "
        "Defaults to DONT_ASK since no user is present to answer prompts.",
    )


class ScheduleRecord(_RecordBase):
    """Persisted schedule record."""

    user_id: str = Field(description="Owner user id.")

    data: ScheduleData = Field(description="Schedule configuration.")
