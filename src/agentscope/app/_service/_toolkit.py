# -*- coding: utf-8 -*-
"""Toolkit assembly for an (agent, session) pair.

The single entry point :func:`get_toolkit` gathers every tool source —
workspace builtins, MCPs, skills, planning tools (Task*), background-task
control (ToolStop), schedule control (Schedule*), team participation
tools, and caller-supplied extras — into one :class:`Toolkit`.
"""
from typing import Any, Literal

from .._manager import BackgroundTaskManager, SchedulerManager
from ..message_bus import MessageBus
from .._tool import (
    AgentCreate,
    AgentInvite,
    TeamCreate,
    TeamDelete,
    TeamSay,
)
from .._types import AgentToolFactory, SubAgentTemplate
from ..storage import AgentRecord, SessionRecord, StorageBase
from ..workspace_manager import WorkspaceManagerBase
from ...middleware import MiddlewareBase
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
    workspace_manager: WorkspaceManagerBase,
    scheduler_manager: SchedulerManager,
    background_task_manager: BackgroundTaskManager,
    message_bus: MessageBus,
    middlewares: list[MiddlewareBase],
    user_id: str,
    agent_record: AgentRecord,
    session_record: SessionRecord,
    extra_factory: AgentToolFactory | None = None,
    sub_agent_templates: dict[str, SubAgentTemplate] | None = None,
) -> Toolkit:
    """Assemble the complete :class:`Toolkit` for one chat turn.

    Tool sources (in attachment order):

    1. Workspace builtins (Bash / Read / Write / Grep / …)
    2. Planning tools (:class:`TaskCreate` / :class:`TaskList` /
       :class:`TaskGet` / :class:`TaskUpdate`)
    3. Background-task control (:class:`ToolStop`, from
       :meth:`BackgroundTaskManager.list_tools`)
    4. Schedule control (:class:`ScheduleCreate` / :class:`ScheduleView`
       / :class:`ScheduleDelete` / :class:`ScheduleList`, from
       :meth:`SchedulerManager.list_tools`). Only attached when the
       session has a model configured (Schedule tools need a model to
       fire new chats with).
    5. Team tools — variant based on the *session's* team role, not
       the agent's ``source``. This matters because a borrowed
       ("invited") agent's session must see worker-only tools even
       though its underlying :class:`AgentRecord` still has
       ``source='user'``. A session that is a worker in some team
       gets only ``TeamSay``. A session that is not in any team OR
       that is its team's leader gets the full leader-side toolset
       (``TeamCreate / AgentCreate / TeamSay / TeamDelete``, plus
       ``AgentInvite`` when the user has at least one invitable agent).
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
            :class:`ToolStop` tool bound to its live task dict.
        message_bus (`MessageBus`):
            Application message bus; passed to team tools so they can
            push HintBlocks + wakeups when delivering inter-session
            messages.
        middlewares (`list[MiddlewareBase]`):
            The agent middlewares that may provide tools to the agent via the
            `list_tools` interface.
        user_id (`str`):
            Caller user id.
        agent_record (`AgentRecord`):
            Pre-loaded agent record (loaded once by the caller). Still
            used for its identity (``id``) and for pipeline consumers
            downstream; the ``source`` field is no longer the team-tool
            gate — see :attr:`session_record.team_id` below.
        session_record (`SessionRecord`):
            Pre-loaded session record (loaded once by the caller).
            Used for the schedule-tool model configuration and — via
            :attr:`SessionRecord.team_id` and the resolved team's
            leader session id — for deciding which team tools to
            attach.
        extra_factory (`AgentToolFactory | None`, optional):
            Async factory invoked once per assembly to produce
            user/session-specific extra tools.
        sub_agent_templates (`dict[str, SubAgentTemplate] | None`, \
optional):
            Sub-agent template registry, keyed by template type.
            Passed to the ``AgentCreate`` tool so it can route to
            the appropriate template when a ``subagent_type`` is
            specified by the leader agent.

    Returns:
        `Toolkit`: Fully populated toolkit (tools + skills + MCPs).
    """

    tool_groups = []

    # The general tools running in the workspace
    tools = await workspace.list_tools()

    # Planning tools — always on.
    tools += [TaskCreate(), TaskList(), TaskGet(), TaskUpdate()]

    # Background-task control.
    tools += await background_task_manager.list_tools(
        session_id=session_record.id,
    )

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

    # Team tools — variant based on the session's team role rather
    # than the agent's ``source`` field. A borrowed ("invited") agent
    # runs with ``source='user'`` on its underlying AgentRecord but
    # its session must behave as a worker; the session-level check
    # captures both created and invited workers uniformly. Sessions
    # not in a team fall through to the leader-side toolset — each
    # leader tool has a runtime precondition check anyway (am I in a
    # team? am I the leader?), so attaching the full set is safe.
    team_tool_kwargs: dict[str, Any] = {
        "storage": storage,
        "message_bus": message_bus,
        "workspace_manager": workspace_manager,
        "user_id": user_id,
        "session_id": session_record.id,
        "agent_id": agent_record.id,
    }
    team_role: Literal["leader", "worker"] | None = None
    if session_record.team_id is not None:
        team = await storage.get_team(user_id, session_record.team_id)
        if team is not None:
            team_role = (
                "leader" if team.session_id == session_record.id else "worker"
            )
    if team_role == "worker":
        tools.append(TeamSay(**team_tool_kwargs, role="worker"))
    else:
        tools += [
            TeamCreate(**team_tool_kwargs),
            AgentCreate(
                **team_tool_kwargs,
                sub_agent_templates=sub_agent_templates or {},
            ),
            TeamSay(**team_tool_kwargs, role="leader"),
            TeamDelete(**team_tool_kwargs),
        ]
        # Conditionally attach AgentInvite. Skipping construction when
        # the user has no invitable agents keeps the input_schema enum
        # non-empty (an empty enum would break tool-schema validators
        # and confuse the LLM into calling a tool with no valid
        # targets). Team-tool base is safe to call for either team or
        # non-team sessions — AgentInvite rechecks the leader
        # precondition at call time.
        invitable_pool = [
            a
            for a in await storage.list_agents(user_id)
            if a.data.invite_config.invitable
            and (a.data.invite_config.invite_description or "").strip()
        ]
        if invitable_pool:
            tools.append(
                AgentInvite(
                    **team_tool_kwargs,
                    invitable_pool=invitable_pool,
                ),
            )

    # Caller-supplied extras.
    if extra_factory is not None:
        tools += await extra_factory(
            user_id,
            agent_record.id,
            session_record.id,
        )

    # Tools from middleware
    for mw in middlewares:
        tools.extend(await mw.list_tools())

    return Toolkit(
        tools=tools,
        skills_or_loaders=await workspace.list_skills(),
        mcps=await workspace.list_mcps(),
        tool_groups=tool_groups,
    )
