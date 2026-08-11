# -*- coding: utf-8 -*-
"""Per-request agent assembly.

:class:`AgentBuilder` constructs an AgentScope agent for each request.
It obtains tools from the :class:`ToolRegistry`, the system prompt
from configuration, and the model from the :class:`ProviderManager`,
then injects all dependencies into the agent constructor.

This is the QwenPaw-style "build per request" pattern — every
request gets a freshly assembled agent so configuration changes
(model switch, tool toggle) take effect immediately.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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

    async def build(
        self,
        ctx: Any,
    ) -> Any:
        """Construct a fully-wired agent for one request.

        Pulls overrides from ``ctx.agent_config`` (sourced from
        :class:`MultiAgentManager`) when available — system prompt,
        max_iters, and tool whitelist all take effect per-agent.
        """
        from agentscope.agent import Agent, ReActConfig
        from agentscope.tool import Toolkit

        cfg = getattr(ctx, "agent_config", None)

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
        # 始终向系统提示追加当前会话身份，便于查询上传文件（见 _build_prompt）。
        sys_prompt = self._with_identity_hint(sys_prompt, ctx)

        # Resolve max_iters (config > default)
        max_iters = getattr(cfg, "max_iters", None) or 20

        # Resolve workspace skills + MCPs + builtins (matches built-in /chat path)
        skills: list = []
        mcps: list = []
        if self._workspace_manager is not None:
            user_id = getattr(ctx, "user_id", "default")
            session_id = getattr(ctx, "session_id", "") or ""
            try:
                workspace = await self._workspace_manager.get_workspace(
                    user_id=user_id,
                    agent_id=getattr(ctx, "agent_id", "default"),
                    session_id=session_id,
                )
                # source 2: workspace builtins (Bash, Read, Write, etc.)
                tools += await workspace.list_tools()
                skills = await workspace.list_skills()
                # source 3: MCP servers (filtered by whitelist)
                all_mcps = await workspace.list_mcps()
                if whitelist:
                    mcps = _filter_by_name(all_mcps, whitelist)
                else:
                    mcps = all_mcps
                ctx.workspace = workspace
            except Exception:
                logger.warning(
                    "builder: workspace load failed, skills/mcps skipped",
                    exc_info=True,
                )

        # Apply whitelist to all tools (builtins + project)
        if whitelist:
            tools = _filter_tools(tools, whitelist)

        toolkit = Toolkit(
            tools=tools,
            skills_or_loaders=skills or None,
            mcps=mcps or None,
        )

        agent = Agent(
            name=getattr(ctx, "agent_id", None) or "default",
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
            "builder: built agent session=%s tools=%d skills=%d mcps=%d middlewares=%d",
            getattr(ctx, "session_id", ""),
            len(tools),
            len(skills),
            len(mcps),
            len(middlewares),
        )
        return agent

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
