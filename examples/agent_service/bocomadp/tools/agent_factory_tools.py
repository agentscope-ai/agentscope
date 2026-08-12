# -*- coding: utf-8 -*-
"""Agent factory tools — used by the agent-creator to manage agent configs.

These tools wrap :class:`MultiAgentManager` CRUD operations and expose
them to the agent-creator as callable functions. Dependencies are injected
via :func:`init_factory_tools` at startup so the tool functions remain
pure-enough for `@tool` decoration.

Tool list:

- ``create_agent``          — create a new agent config
- ``update_agent``          — update an existing agent config
- ``delete_agent``          — delete an agent config
- ``list_agents``           — list all agents
- ``get_agent``             — get one agent's full config
- ``list_tools_for_agent``  — list all available tools + MCPs in the system
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("bocomadp.agent_factory_tools")

try:
    from agentscope.tool import tool
except ImportError:
    def tool(*args, **kwargs):  # type: ignore
        """Fallback @tool decorator when agentscope is not installed."""
        if len(args) == 1 and callable(args[0]):
            return args[0]

        def decorator(fn):
            return fn

        return decorator


# ---------------------------------------------------------------------------
# Module-level state — populated by :func:`init_factory_tools` at startup
# ---------------------------------------------------------------------------
_agent_manager: Any = None
_tool_registry: Any = None
_mcp_registry: Any = None


def init_factory_tools(
    agent_manager: Any,
    tool_registry: Any = None,
    mcp_registry: Any = None,
) -> None:
    """Wire the factory tools to live registries.

    Call once at startup, before any agent-creator conversation.

    Args:
        agent_manager: :class:`MultiAgentManager` instance.
        tool_registry: :class:`ToolRegistry` instance.
        mcp_registry: :class:`McpRegistry` instance.
    """
    global _agent_manager, _tool_registry, _mcp_registry
    _agent_manager = agent_manager
    _tool_registry = tool_registry
    _mcp_registry = mcp_registry
    logger.info(
        "agent_factory_tools initialized: agents=%d tools=%d mcps=%d",
        len(_agent_manager.list_agents()) if _agent_manager else 0,
        len(_tool_registry.list_tool_names()) if _tool_registry else 0,
        len(_mcp_registry.list_mcps()) if _mcp_registry else 0,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _check_init() -> str | None:
    """Return an error string if deps are not wired, else None."""
    if _agent_manager is None:
        return "agent_factory_tools 未初始化，请联系管理员检查服务启动配置"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def create_agent(
    agent_id: str,
    name: str,
    system_prompt: str,
    enabled_tools: list[str] | None = None,
    max_iters: int = 20,
) -> str:
    """创建一个新的智能体配置。

    智能体配置包含：
    - agent_id: 在系统中唯一标识该智能体，后续对话和修改时使用
    - name: 给人看的显示名称
    - system_prompt: 决定智能体行为的核心提示词
    - enabled_tools: 该智能体可以使用的工具列表（设为空列表 [] 表示全部可用）
    - max_iters: 最大推理轮次（默认20，复杂任务可设30~50）

    Args:
        agent_id (str): 唯一标识符，建议小写英文+连字符，如 'customer-service'
        name (str): 显示名称
        system_prompt (str): 系统提示词
        enabled_tools (list[str] | None): 启用的工具名称列表，None或[]表示全部
        max_iters (int): 最大推理轮次
    """
    err = _check_init()
    if err:
        return err

    try:
        from bocomadp.routers.agent_manage import AgentConfigRequest

        config = AgentConfigRequest(
            agent_id=agent_id,
            name=name,
            system_prompt=system_prompt,
            enabled_tools=enabled_tools or [],
            max_iters=max_iters,
        )
        agent = _agent_manager.create_agent(config)
        return json.dumps(agent.model_dump(), ensure_ascii=False, indent=2)
    except ValueError as e:
        return f"创建失败: {e}"


@tool
def update_agent(
    agent_id: str,
    name: str = "",
    system_prompt: str = "",
    enabled_tools: list[str] | None = None,
    max_iters: int | None = None,
) -> str:
    """修改已有智能体的配置。未传入的字段保持原值不变。

    先调用 get_agent 查看当前配置，再决定修改哪些字段。

    Args:
        agent_id (str): 要修改的智能体标识
        name (str): 新的显示名称（空字符串表示不改）
        system_prompt (str): 新的系统提示词（空字符串表示不改）
        enabled_tools (list[str] | None): 新的工具列表（None表示不改，[]表示全部启用）
        max_iters (int | None): 新的最大轮次（None表示不改）
    """
    err = _check_init()
    if err:
        return err

    existing = _agent_manager.get_agent(agent_id)
    if existing is None:
        return f"智能体 '{agent_id}' 不存在。先调用 list_agents 查看所有已创建的智能体。"

    try:
        from bocomadp.routers.agent_manage import AgentConfigRequest

        config = AgentConfigRequest(
            agent_id=agent_id,
            name=name or existing.name,
            system_prompt=system_prompt or existing.system_prompt,
            enabled_tools=(
                enabled_tools
                if enabled_tools is not None
                else existing.enabled_tools
            ),
            max_iters=max_iters if max_iters is not None else existing.max_iters,
            requires_sandbox=getattr(existing, "requires_sandbox", True),
        )
        agent = _agent_manager.update_agent(agent_id, config)
        return json.dumps(agent.model_dump(), ensure_ascii=False, indent=2)
    except KeyError as e:
        return f"修改失败: {e}"


@tool
def delete_agent(agent_id: str) -> str:
    """删除一个智能体配置。默认智能体（default）和智能体工厂自身（agent-creator）不可删除。

    Args:
        agent_id (str): 要删除的智能体标识
    """
    err = _check_init()
    if err:
        return err

    if agent_id.startswith("_") or agent_id == "default":
        return f"智能体 '{agent_id}' 是系统内置的，不可删除。"

    try:
        _agent_manager.delete_agent(agent_id)
        return f"智能体 '{agent_id}' 已删除。"
    except ValueError as e:
        return f"删除失败: {e}"


@tool
def list_agents() -> str:
    """列出系统中所有智能体的摘要信息（agent_id、name、工具数）。"""
    err = _check_init()
    if err:
        return err

    agents = _agent_manager.list_agents()
    if not agents:
        return "当前没有任何智能体配置。"

    lines = [f"共 {len(agents)} 个智能体:\n"]
    for a in agents:
        tools_preview = ", ".join(a.enabled_tools[:5])
        if len(a.enabled_tools) > 5:
            tools_preview += f" ...(+{len(a.enabled_tools)-5})"
        if not a.enabled_tools:
            tools_preview = "全部可用"
        lines.append(
            f"- {a.agent_id:30} {a.name:20} "
            f"tools=[{tools_preview}] max_iters={a.max_iters}",
        )
    return "\n".join(lines)


@tool
def get_agent(agent_id: str) -> str:
    """查看指定智能体的完整配置，包括 system prompt、工具列表等。

    Args:
        agent_id (str): 智能体标识
    """
    err = _check_init()
    if err:
        return err

    agent = _agent_manager.get_agent(agent_id)
    if agent is None:
        return f"智能体 '{agent_id}' 不存在。先调用 list_agents 查看所有已创建的智能体。"

    return json.dumps(agent.model_dump(), ensure_ascii=False, indent=2)


@tool
def list_tools_for_agent() -> str:
    """列出系统中所有可分配给智能体的工具和MCP服务器。

    返回两部分：
    - tools: 项目工具和框架内置工具的名称+描述
    - mcps: MCP 服务器列表
    """
    tools_info: list[str] = []
    mcps_info: list[str] = []

    # Builtin tools всегда доступны
    _BUILTIN_TOOLS = [
        {"name": "bash", "description": "在沙箱中执行Shell命令"},
        {"name": "read", "description": "读取文件内容"},
        {"name": "write", "description": "写入文件"},
        {"name": "edit", "description": "精确编辑文件"},
        {"name": "glob", "description": "按通配符模式查找文件"},
        {"name": "grep", "description": "在文件中搜索文本"},
    ]

    tools_info.append("\n## 框架内置工具")
    for bt in _BUILTIN_TOOLS:
        tools_info.append(f"- {bt['name']:20} {bt['description']}")

    # Project tools
    if _tool_registry is not None:
        tools_info.append("\n## 项目工具")
        for name in _tool_registry.list_tool_names():
            tools_info.append(f"- {name}")

    # MCP servers
    if _mcp_registry is not None:
        mcps = _mcp_registry.list_mcps()
        if mcps:
            mcps_info.append("\n## MCP 服务器")
            for mcp in mcps:
                mcp_name = getattr(mcp, "name", "") or ""
                mcp_desc = getattr(mcp, "description", None) or ""
                mcps_info.append(f"- {mcp_name}: {mcp_desc}" if mcp_desc else f"- {mcp_name}")

    parts = ["# 系统可用工具一览", "\n".join(tools_info)]
    if mcps_info:
        parts.append("\n".join(mcps_info))

    return "\n".join(parts)


__all__ = ["init_factory_tools"]
