# -*- coding: utf-8 -*-
"""Shared type aliases for the agentscope app layer."""
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from ..agent import ContextConfig, ReActConfig
from ..middleware import MiddlewareBase
from ..permission import PermissionContext
from ..state import TaskContext
from ..tool import ToolBase


AgentMiddlewareFactory = Callable[
    [str, str, str],
    Awaitable[list[MiddlewareBase]],
]
# Async factory signature: ``(user_id, agent_id, session_id)`` →
# awaitable of :class:`~agentscope.middleware.MiddlewareBase` instances.

AgentToolFactory = Callable[
    [str, str, str],
    Awaitable[list[ToolBase]],
]
#  Async factory signature: ``(user_id, agent_id, session_id)`` →
#  awaitable of :class:`~agentscope.tool.ToolBase` instances.


class SubAgentTemplate(BaseModel):
    """A reusable blueprint for sub-agent creation within a team.

    Developers register one or more templates at the ``create_app`` entry
    point. When the leader agent calls ``AgentCreate`` with a matching
    ``subagent_type``, the template's configuration is used instead of the
    built-in defaults.

    The :attr:`type` field serves as the routing key — it becomes an enum
    value of the ``subagent_type`` parameter exposed to the LLM.  This is
    distinct from the ``name`` parameter in ``AgentCreate``, which is the
    per-instance identifier the leader assigns to each worker (used for
    ``TeamSay(to=name)``).

    All fields are pure data (no callables), so the template is fully
    serializable for future config-driven startup.
    """

    type: str = Field(
        description=(
            "Template type identifier, e.g. ``'researcher'`` or "
            "``'coder'``. Used as the enum value for the "
            "``subagent_type`` parameter in ``AgentCreate``."
        ),
    )

    description: str = Field(
        description=(
            "Agent-readable description of this sub-agent type. "
            "Exposed to the LLM in the ``AgentCreate`` tool schema "
            "so it can choose the appropriate type."
        ),
    )

    system_prompt_template: str = Field(
        description=(
            "A Python format string for the worker's system prompt. "
            "Available placeholders: ``{team_name}``, "
            "``{team_description}``, ``{member_name}``, "
            "``{member_description}``, ``{leader_name}``."
        ),
    )

    context_config: ContextConfig = Field(
        default_factory=ContextConfig,
        description="Context configuration for the sub-agent.",
    )

    react_config: ReActConfig = Field(
        default_factory=ReActConfig,
        description="ReAct loop configuration for the sub-agent.",
    )

    permission_context: PermissionContext = Field(
        default_factory=PermissionContext,
        description=(
            "Permission context applied to the sub-agent at "
            "creation time. Controls what the worker is allowed "
            "to do (e.g. read-only vs. full access)."
        ),
    )

    tasks_context: TaskContext = Field(
        default_factory=TaskContext,
        description=(
            "Pre-defined task context for the sub-agent, allowing "
            "the template to seed an initial workflow."
        ),
    )
