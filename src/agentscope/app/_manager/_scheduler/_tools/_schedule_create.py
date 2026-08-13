# -*- coding: utf-8 -*-
"""The schedule create tool."""
from datetime import datetime
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

    name: str = Field(description="调度的显示名称。")

    description: str = Field(
        default="",
        description="调度的描述，包括其用途。",
    )

    cron_expression: str = Field(
        description="标准 5 段 cron 表达式，例如 '0 9 * * 1-5'。",
    )

    timezone: str = Field(
        default="UTC",
        description="用于计算 cron 表达式的 IANA 时区名称，"
        "例如 'America/New_York' 或 'Asia/Shanghai'。",
    )

    enabled: bool = Field(
        default=True,
        description="调度在创建后是否立即生效。"
        "设置为 False 可创建禁用的调度。",
    )

    started_at: datetime | None = Field(
        default=None,
        description="调度生效的 ISO-8601 日期时间。"
        "未指定时默认为当前时间。",
    )

    ended_at: datetime | None = Field(
        default=None,
        description="调度停止触发的 ISO-8601 日期时间。"
        "若未设置，调度将无限期运行。",
    )

    stateful: bool = Field(
        default=False,
        description="若为 True，连续执行共享同一会话上下文。"
        "若为 False，每次执行都会获得全新的会话。",
    )

    permission_mode: str = Field(
        default=PermissionMode.DONT_ASK.value,
        description=(
            "调度执行期间智能体的权限模式。"
            f"允许的值：{[m.value for m in PermissionMode]}。"
            "由于没有用户在场，默认为 'dont_ask'。"
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

    description: str = """为自己创建新的周期性定时任务。\
每次调度被触发时，你都会在一个新会话中被通知。

**关于 cron 表达式：**
- 首先确定你当前的时区，这对设置正确的 cron 表达式非常重要。\
可以通过 bash 命令（如 `date +%z`、`cat /etc/timezone`）获取，\
或直接询问用户。
- 确定任务是只运行一次还是按固定间隔重复运行，然后据此设置 cron 表达式。
- 对于一次性任务，先查询当前时间，再将 cron 表达式设置为\
在该特定时刻触发。
- 将 `started_at` 和 `ended_at` 设置为符合用户的要求。\
如有疑问，请在创建调度前先请求澄清。

**关于 description 字段：**
- 当调度在新会话中触发时，`description` 是你唯一可用的上下文。\
请包含所有必要细节：目标、预期输出、约束条件、相关文件路径，\
以及独立完成该任务所需的任何其他信息。
"""

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
        enabled: bool = True,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
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
            enabled (`bool`, optional):
                Whether the schedule is active immediately after creation.
            started_at (`datetime | None`, optional):
                Datetime at which the schedule becomes active. Defaults to
                the current time when not specified.
            ended_at (`datetime | None`, optional):
                Datetime at which the schedule stops firing. If not set the
                schedule runs indefinitely.
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
                enabled=enabled,
                cron_expression=cron_expression,
                timezone=timezone,
                started_at=started_at or datetime.now(),
                ended_at=ended_at,
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
                        f"Enabled: {enabled}\n"
                        f"Started at: {record.data.started_at}\n"
                        f"Ended at: {ended_at or '(no end time)'}\n"
                        f"Stateful: {stateful}"
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
