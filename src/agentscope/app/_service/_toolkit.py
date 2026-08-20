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
    ToolBase,
    Toolkit,
    ToolGroup,
)
from ...workspace import WorkspaceBase
from ..access import ResourceKind
from ._access import ResourceAccessService


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
    resource_access_service: ResourceAccessService,
    extra_factory: AgentToolFactory | None = None,
    sub_agent_templates: dict[str, SubAgentTemplate] | None = None,
    team_role: Literal["leader", "worker"] | None = None,
    channel_tools: list[ToolBase] | None = None,
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
    5. Team tools — by caller-resolved ``team_role``: a worker gets only
       ``TeamSay``; anyone else gets the full leader-side toolset.
    6. Caller-supplied extras (``extra_factory``)
    7. Channel platform tools — the caller resolves them (once, shared
       with the system-prompt attachment) and passes ``channel_tools``.

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
            gate — see ``team_role`` below.
        session_record (`SessionRecord`):
            Pre-loaded session record (loaded once by the caller).
            Used for the schedule-tool model configuration.
        extra_factory (`AgentToolFactory | None`, optional):
            Async factory invoked once per assembly to produce
            user/session-specific extra tools.
        sub_agent_templates (`dict[str, SubAgentTemplate] | None`, \
optional):
            Sub-agent template registry, keyed by template type.
            Passed to the ``AgentCreate`` tool so it can route to
            the appropriate template when a ``subagent_type`` is
            specified by the leader agent.
        team_role (`Literal["leader", "worker"] | None`, optional):
            The session's team role, resolved once by the caller.
            ``None`` means not in any team.
        channel_tools (`list[ToolBase] | None`, optional):
            Platform tools of the originating channel, resolved once
            by the caller. ``None`` / empty when channel-less.

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

    # Team tools by caller-resolved role; non-team sessions get the
    # leader set (each leader tool rechecks its precondition at call time).
    team_tool_kwargs: dict[str, Any] = {
        "storage": storage,
        "message_bus": message_bus,
        "workspace_manager": workspace_manager,
        "user_id": user_id,
        "session_id": session_record.id,
        "agent_id": agent_record.id,
    }
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
        #
        # Walk agents *visible* to the caller (own + shared through the
        # resource access policy) so a leader can invite a partner's
        # agent when the policy grants access.
        visible_agents = await resource_access_service.list_resource(
            user_id,
            ResourceKind.AGENT,
        )
        invitable_pool = [
            view
            for view in visible_agents
            if view.data.invite_config.invitable
            and (view.data.invite_config.invite_description or "").strip()
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

    # Channel platform tools, resolved once by the caller (also feeds
    # the channel section of the system prompt).
    if channel_tools:
        tools += channel_tools

    return Toolkit(
        tools=tools,
        skills_or_loaders=await workspace.list_skills(
            agent_id=agent_record.id,
        ),
        mcps=await workspace.list_mcps(
            agent_id=agent_record.id,
            session_id=session_record.id,
        ),
        tool_groups=tool_groups,
    )
