# -*- coding: utf-8 -*-
"""Toolkit assembly for an (agent, session) pair.

The single entry point :func:`get_toolkit` gathers every tool source —
workspace builtins, MCPs, skills, planning tools (Task*), background-task
control (TaskStop), schedule control (Schedule*), team participation
tools, and caller-supplied extras — into one :class:`Toolkit`.
"""
from typing import Any

from .._manager import BackgroundTaskManager, SchedulerManager
from ..message_bus import MessageBus
from .._tools import AgentCreate, TeamCreate, TeamDelete, TeamSay
from .._types import AgentToolFactory
from ..storage import AgentRecord, SessionRecord, StorageBase
from ...tool import (
    TaskCreate,
    TaskGet,
    TaskList,
    TaskUpdate,
    Toolkit,
    ToolGroup,
)
from ...workspace import WorkspaceBase


async def get_toolkit(
    *,
    storage: StorageBase,
    workspace: WorkspaceBase,
    scheduler_manager: SchedulerManager,
    background_task_manager: BackgroundTaskManager,
    message_bus: MessageBus,
    user_id: str,
    agent_record: AgentRecord,
    session_record: SessionRecord,
    extra_factory: AgentToolFactory | None = None,
) -> Toolkit:
    """Assemble the complete :class:`Toolkit` for one chat turn.

    Tool sources (in attachment order):

    1. Workspace builtins (Bash / Read / Write / Grep / …)
    2. Planning tools (:class:`TaskCreate` / :class:`TaskList` /
       :class:`TaskGet` / :class:`TaskUpdate`)
    3. Background-task control (:class:`TaskStop`, from
       :meth:`BackgroundTaskManager.list_tools`)
    4. Schedule control (:class:`ScheduleCreate` / :class:`ScheduleView`
       / :class:`ScheduleDelete` / :class:`ScheduleList`, from
       :meth:`SchedulerManager.list_tools`). Only attached when the
       session has a model configured (Schedule tools need a model to
       fire new chats with).
    5. Team tools — selected inline by ``agent_record.source``:
       worker (``"team"``) gets only ``TeamSay``; everyone else gets
       the full leader-side toolset
       (``TeamCreate / AgentCreate / TeamSay / TeamDelete``)
    6. Caller-supplied extras (``extra_factory``)

    Plus the workspace's skills and MCPs, which become the toolkit's
    ``skills_or_loaders`` and ``mcps`` parameters.

    Args:
        storage (`StorageBase`):
            Application storage backend; needed by team tools to read
            fresh team / session state at call time, and by schedule
            tools.
        workspace (`WorkspaceBase`):
            Pre-resolved per-session workspace (caller resolves it
            via :meth:`WorkspaceManagerBase.get_workspace`). Used here
            for tool / skill / MCP discovery.
        scheduler_manager (`SchedulerManager`):
            Application scheduler. Provides the four schedule tools and
            persists schedules through it.
        background_task_manager (`BackgroundTaskManager`):
            Application background-task registry. Provides the
            :class:`TaskStop` tool bound to its live task dict.
        message_bus (`MessageBus`):
            Application message bus; passed to team tools so they can
            push HintBlocks + wakeups when delivering inter-session
            messages.
        user_id (`str`):
            Caller user id.
        agent_record (`AgentRecord`):
            Pre-loaded agent record (loaded once by the caller). Its
            ``source`` field determines which team tools are attached.
        session_record (`SessionRecord`):
            Pre-loaded session record (loaded once by the caller).
            Used for the schedule-tool model configuration.
        extra_factory (`AgentToolFactory | None`, optional):
            Async factory invoked once per assembly to produce
            user/session-specific extra tools.

    Returns:
        `Toolkit`: Fully populated toolkit (tools + skills + MCPs).
    """

    tool_groups = []

    # The general tools running in the workspace
    tools = await workspace.list_tools()

    # Planning tools — always on.
    tools += [TaskCreate(), TaskList(), TaskGet(), TaskUpdate()]

    # Background-task control.
    tools += await background_task_manager.list_tools()

    # Schedule control. Requires a model config on this session because
    # ``ScheduleCreate`` records it into new ``ScheduleRecord`` instances.
    if session_record.config.chat_model_config is not None:
        # Add schedule tools as a tool group
        tool_groups.append(
            ToolGroup(
                name="schedule_tools",
                description=(
                    """Tools for managing cron schedules. A cron schedule is \
a recurring task that fires at a specified time — at that point, a new \
session is created and an agent will be invoked to complete the given task \
autonomously.

## When to Use This Tool Group
- When you need to create a new cron schedule that triggers at a specific \
time or interval"
- When you're asked to list, inspect, stop, or delete existing cron schedules
"""
                ),
                tools=await scheduler_manager.list_tools(
                    user_id=user_id,
                    agent_id=agent_record.id,
                    chat_model_config=session_record.config.chat_model_config,
                ),
            ),
        )

    # Team tools — variant based on ``agent_record.source``. A worker
    # only gets TeamSay (to report back); a user-owned agent always
    # gets the full leader-side toolset. Each tool checks its own
    # preconditions (am I in a team? am I the leader?) at call time
    # against fresh storage, which is why the full set can be attached
    # unconditionally without needing a stale snapshot of team_id.
    team_tool_kwargs: dict[str, Any] = {
        "storage": storage,
        "message_bus": message_bus,
        "user_id": user_id,
        "session_id": session_record.id,
        "agent_id": agent_record.id,
    }
    if agent_record.source == "team":
        tools.append(TeamSay(**team_tool_kwargs, role="worker"))
    else:
        tools += [
            TeamCreate(**team_tool_kwargs),
            AgentCreate(**team_tool_kwargs),
            TeamSay(**team_tool_kwargs, role="leader"),
            TeamDelete(**team_tool_kwargs),
        ]

    # Caller-supplied extras.
    if extra_factory is not None:
        tools += await extra_factory(
            user_id,
            agent_record.id,
            session_record.id,
        )

    return Toolkit(
        tools=tools,
        skills_or_loaders=await workspace.list_skills(),
        mcps=await workspace.list_mcps(),
        tool_groups=tool_groups,
    )
