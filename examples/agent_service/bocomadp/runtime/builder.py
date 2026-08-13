# -*- coding: utf-8 -*-
"""Per-request agent assembly.

:class:`AgentBuilder` constructs an AgentScope agent for each request.
It obtains tools from the :class:`ToolRegistry`, the system prompt
from configuration, and the model from the :class:`ProviderManager`,
then injects all dependencies into the agent constructor.

This is the QwenPaw-style "build per request" pattern — every
request gets a freshly assembled agent so configuration changes
(model switch, tool toggle) take effect immediately.

No-sandbox agents
-----------------
Agents with ``requires_sandbox=False`` skip workspace (K8s Pod)
creation entirely.  Their skills are loaded from the host filesystem
via :class:`~agentscope.skill.LocalSkillLoader`, and factory tools
(``agent_factory_tools``) are injected for the built-in
``agent-creator`` agent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve the host skill directory once at import time.
# Relative to this builder module:  ../../skills  (bocomadp/skills/)
# ---------------------------------------------------------------------------
_HOST_SKILL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills",
)


class AgentBuilder:
    """Compose an agent for each request.

    The builder pulls tools from the :class:`ToolRegistry`, middlewares
    from the :class:`MiddlewareRegistry`, and the model from the
    :class:`ProviderManager`. All dependencies are injected externally.

    When a :class:`WorkspaceManagerBase` is supplied, the builder also
    loads workspace-level skills and MCP clients so the agent sees the
    same skill instructions the built-in ``/chat`` path provides.
    """

    def __init__(
        self,
        *,
        tool_registry: Any = None,
        middleware_registry: Any = None,
        provider_manager: Any = None,
        workspace_manager: Any = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._middleware_registry = middleware_registry
        self._provider_manager = provider_manager
        self._workspace_manager = workspace_manager
        # Host skill loader — built lazily on first use
        self._host_skill_loader: Any = None

    async def build(
        self,
        ctx: Any,
    ) -> Any:
        """Construct a fully-wired agent for one request.

        Pulls overrides from ``ctx.agent_config`` (sourced from
        :class:`MultiAgentManager`) when available — system prompt,
        max_iters, tool whitelist, and sandbox preference all take
        effect per-agent.
        """
        from agentscope.agent import Agent, ReActConfig
        from agentscope.tool import Toolkit

        cfg = getattr(ctx, "agent_config", None)
        agent_id = getattr(ctx, "agent_id", "") or "default"
        # Framework agents default to sandbox, but the built-in
        # agent-creator runs without one (host skills + factory tools).
        requires_sandbox = (
            False if agent_id == "_agent-creator"
            else getattr(cfg, "requires_sandbox", True)
        )

        # Resolve tools (apply whitelist across all sources)
        whitelist: list[str] = getattr(cfg, "enabled_tools", None) or []
        tools: list = []

        # source 1: project tools from ToolRegistry
        if self._tool_registry is not None:
            tools = self._tool_registry.list_tools()

        # Resolve model
        model = None
        if self._provider_manager is not None:
            model = self._provider_manager.get_model()
        if model is None:
            raise RuntimeError(
                "No model configured; set one via ProviderManager",
            )

        # Resolve middlewares
        middlewares = []
        if self._middleware_registry is not None:
            middlewares = self._middleware_registry.list_middlewares()

        # Resolve system prompt (config > fallback)
        sys_prompt = getattr(cfg, "system_prompt", None) or self._build_prompt(ctx)

        # Resolve max_iters (config > default)
        max_iters = getattr(cfg, "max_iters", None) or 20

        # Resolve workspace skills + MCPs + builtins
        skills: list = []
        mcps: list = []

        if requires_sandbox:
            # ── [normal] sandbox path: create K8s Pod workspace ──
            sys_prompt = self._with_identity_hint(sys_prompt, ctx)
            if self._workspace_manager is not None:
                user_id = getattr(ctx, "user_id", "default")
                session_id = getattr(ctx, "session_id", "") or ""
                try:
                    workspace = await self._workspace_manager.get_workspace(
                        user_id=user_id,
                        agent_id=agent_id,
                        session_id=session_id,
                    )
                    # source 2: workspace builtins (Bash, Read, Write, etc.)
                    tools += await workspace.list_tools()
                    skills = await workspace.list_skills()
                    # source 3: MCP servers (filtered by whitelist)
                    all_mcps = await workspace.list_mcps()
                    if whitelist:
                        mcps = self._filter_by_name(all_mcps, whitelist)
                    else:
                        mcps = all_mcps
                    ctx.workspace = workspace
                except Exception:
                    logger.warning(
                        "builder: workspace load failed, skills/mcps skipped",
                        exc_info=True,
                    )
        else:
            # ── [no-sandbox] host path: no K8s Pod ──
            logger.info(
                "builder: no-sandbox agent=%s, loading host skills",
                agent_id,
            )
            # Load skills from host filesystem
            skills = await self._load_host_skills()

            # Inject factory tools for the built-in agent-creator
            if agent_id == "_agent-creator":
                factory_tools = self._get_factory_tools()
                if factory_tools:
                    tools.extend(factory_tools)
                    logger.info(
                        "builder: injected %d factory tools for agent-creator",
                        len(factory_tools),
                    )

        # Apply whitelist to all tools (builtins + project + factory)
        if whitelist:
            tools = self._filter_tools(tools, whitelist)

        toolkit = Toolkit(
            tools=tools,
            skills_or_loaders=skills or None,
            mcps=mcps or None,
        )

        agent = Agent(
            name=agent_id,
            model=model,
            system_prompt=sys_prompt,
            toolkit=toolkit,
            react_config=ReActConfig(max_iters=max_iters),
            middlewares=middlewares,
        )

        # Load session state if available
        if ctx.session_state:
            agent.load_state_dict(ctx.session_state)

        logger.info(
            "builder: built agent session=%s tools=%d skills=%d mcps=%d middlewares=%d sandbox=%s",
            getattr(ctx, "session_id", ""),
            len(tools),
            len(skills),
            len(mcps),
            len(middlewares),
            requires_sandbox,
        )
        return agent

    # ------------------------------------------------------------------
    # Host skill loading
    # ------------------------------------------------------------------

    async def _load_host_skills(self) -> list:
        """Load skills from the host filesystem via :class:`LocalSkillLoader`.

        Scans ``bocomadp/skills/`` recursively for ``SKILL.md`` files.
        The loader is initialized lazily and cached.
        """
        try:
            from agentscope.skill import LocalSkillLoader
        except ImportError:
            logger.warning("builder: LocalSkillLoader not available")
            return []

        if self._host_skill_loader is None:
            if not os.path.isdir(_HOST_SKILL_DIR):
                logger.warning(
                    "builder: host skill dir not found: %s",
                    _HOST_SKILL_DIR,
                )
                return []
            self._host_skill_loader = LocalSkillLoader(
                _HOST_SKILL_DIR,
                scan_subdir=True,
            )
            logger.info(
                "builder: host skill loader initialized dir=%s",
                _HOST_SKILL_DIR,
            )

        return await self._host_skill_loader.list_skills()

    # ------------------------------------------------------------------
    # Factory tools (agent-creator only)
    # ------------------------------------------------------------------

    @staticmethod
    def _get_factory_tools() -> list:
        """Return agent factory tool functions.

        These are NOT registered in the global :class:`ToolRegistry` —
        they are injected only into the built-in ``agent-creator`` agent
        so no other agent can accidentally call them.
        """
        try:
            from bocomadp.tools.agent_factory_tools import (
                create_agent,
                update_agent,
                delete_agent,
                list_agents,
                get_agent,
                list_tools_for_agent,
                set_agent_tools,
                list_available_skills,
                enable_skill_for_agent,
            )

            return [
                create_agent,
                update_agent,
                delete_agent,
                list_agents,
                get_agent,
                list_tools_for_agent,
                set_agent_tools,
                list_available_skills,
                enable_skill_for_agent,
            ]
        except ImportError:
            logger.warning(
                "builder: agent_factory_tools not available, "
                "agent-creator will have no factory tools",
            )
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_tools(tools: list, whitelist: list[str]) -> list:
        """Keep only tools whose name appears in *whitelist*."""
        result = []
        for t in tools:
            name = (
                getattr(t, "name", None)
                or getattr(getattr(t, "func", None), "__name__", "")
                or getattr(t, "__name__", "")
            )
            if name in whitelist:
                result.append(t)
        return result

    @staticmethod
    def _filter_by_name(items: list, whitelist: list[str]) -> list:
        """Keep items whose ``.name`` attribute appears in *whitelist*."""
        return [item for item in items if getattr(item, "name", "") in whitelist]

    def _build_prompt(self, ctx: Any) -> str:
        """Default system prompt when no agent config is set."""
        return (
            "You are a helpful AI assistant. "
            "Use the available tools to answer user questions."
        )

    @staticmethod
    def _with_identity_hint(sys_prompt: str, ctx: Any) -> str:
        """向系统提示追加当前会话身份，便于查询本会话上传的文件。

        模型在用户询问「我上传了哪些文件」时，需知道 user_id 与 session_id
        才能调用 list_uploaded_files。这两个值来自运行时 ctx，无法从工具侧
        自动获取，因此在此始终注入（无论是否使用 agent_config 的提示）。
        """
        user_id = getattr(ctx, "user_id", "") or "default"
        session_id = getattr(ctx, "session_id", "") or ""
        identity_hint = (
            "\n\n[当前会话身份]\n"
            f"user_id = {user_id}\n"
            f"session_id = {session_id}\n"
            "查询本会话用户上传的文件时，请直接用以上 user_id 与 session_id "
            "调用 list_uploaded_files；若消息中提供了 virtual_path，也可直接传入。"
        )
        return sys_prompt + identity_hint


__all__ = ["AgentBuilder"]
