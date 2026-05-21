# -*- coding: utf-8 -*-
"""The schedule create tool."""
from typing import Any

from pydantic import BaseModel, Field

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
)
from .....state import AgentState
from .....tool import ToolBase, ToolChunk
from ....storage import (
    ScheduleData,
    ScheduleRecord,
    ScheduleSource,
    ChatModelConfig,
)


class _ScheduleCreateParams(BaseModel):
    """The params for the schedule create tool."""

    name: str = Field(description="Display name of the schedule.")

    description: str = Field(
        default="",
        description="Description of the schedule, including its purpose.",
    )

    cron_expression: str = Field(
        description="Standard 5-field cron expression, e.g. '0 9 * * 1-5'.",
    )

    timezone: str = Field(
        default="UTC",
        description="IANA timezone name used to evaluate the cron expression, "
        "e.g. 'America/New_York' or 'Asia/Shanghai'.",
    )

    stateful: bool = Field(
        default=False,
        description="If True, consecutive executions share the same session "
        "context. If False, each execution gets a fresh session.",
    )

    permission_mode: str = Field(
        default=PermissionMode.DONT_ASK.value,
        description=(
            "Permission mode for the agent during scheduled execution. "
            f"Allowed values: {[m.value for m in PermissionMode]}. "
            "Defaults to 'dont_ask' since no user is present."
        ),
    )


class ScheduleCreate(ToolBase):
    """The schedule create tool.

    Creates a new scheduled task that will execute the current agent at a
    given cron interval.  The record is persisted to storage and immediately
    registered with the in-memory APScheduler.

    The schedule inherits the model configuration of the current session.
    The agent that creates the schedule is also the agent that will be run
    on each trigger.
    """

    name: str = "ScheduleCreate"

    description: str = (
        "Create a new recurring scheduled task for the current agent. "
        "The agent will be executed automatically according to the given "
        "cron expression. Returns the new schedule ID on success."
    )

    input_schema: dict = _ScheduleCreateParams.model_json_schema()

    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_state_injected: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        user_id: str,
        agent_id: str,
        chat_model_config: ChatModelConfig,
        storage: Any,
        scheduler_manager: Any,
    ) -> None:
        """Initialize the schedule create tool.

        Args:
            user_id (`str`):
                The authenticated user who owns this schedule.
            agent_id (`str`):
                The agent that will be executed on each trigger.
            chat_model_config (`ChatModelConfig`):
                Model configuration inherited from the current session.
            storage (`Any`):
                The storage backend used to persist the schedule record.
            scheduler_manager (`Any`):
                The scheduler manager used to register the APScheduler job.
                Must expose a ``register_schedule(record)`` coroutine.
        """
        self._user_id = user_id
        self._agent_id = agent_id
        self._chat_model_config = chat_model_config
        self._storage = storage
        self._scheduler_manager = scheduler_manager

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permission for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed to be called.",
        )

    async def __call__(  # type: ignore[override]
        self,
        name: str,
        cron_expression: str,
        description: str = "",
        timezone: str = "UTC",
        stateful: bool = False,
        permission_mode: str = PermissionMode.DONT_ASK.value,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        """Create a new scheduled task.

        Args:
            name (`str`):
                Display name of the schedule.
            cron_expression (`str`):
                Standard 5-field cron expression, e.g. ``'0 9 * * 1-5'``.
            description (`str`, optional):
                Human-readable description of what this schedule does.
            timezone (`str`, optional):
                IANA timezone name, e.g. ``'Asia/Shanghai'``.
            stateful (`bool`, optional):
                Whether consecutive executions share the same session context.
            permission_mode (`str`, optional):
                Permission mode value string.
            _agent_state (`AgentState | None`, optional):
                Injected agent state; provides the source session ID.

        Returns:
            `ToolChunk`:
                A chunk with the new schedule ID on success, or an error
                description on failure.
        """
        try:
            perm_mode = PermissionMode(permission_mode)
        except ValueError:
            perm_mode = PermissionMode.DONT_ASK

        source_session_id = (
            _agent_state.session_id if _agent_state is not None else ""
        )

        record = ScheduleRecord(
            user_id=self._user_id,
            agent_id=self._agent_id,
            data=ScheduleData(
                name=name,
                description=description,
                cron_expression=cron_expression,
                timezone=timezone,
                stateful=stateful,
                permission_mode=perm_mode,
                source=ScheduleSource.AGENT,
                source_session_id=source_session_id,
                chat_model_config=self._chat_model_config,
            ),
        )

        await self._storage.upsert_schedule(self._user_id, record)
        await self._scheduler_manager.register_schedule(record)

        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"Schedule {name!r} created successfully.\n"
                        f"Schedule ID: {record.id}\n"
                        f"Cron: {cron_expression} (timezone: {timezone})\n"
                        f"Stateful: {stateful}"
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
