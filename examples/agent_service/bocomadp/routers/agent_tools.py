# -*- coding: utf-8 -*-
"""Agent tool management — query & toggle per-agent tool enablement.

Endpoints
---------
``GET    /api/agents/{agent_id}/tools``           — list tools with status
``PUT    /api/agents/{agent_id}/tools/{name}``    — enable a tool
``DELETE /api/agents/{agent_id}/tools/{name}``    — disable a tool

Tool sources (matching the full ``get_toolkit()`` assembly):

1. **Workspace builtins** — Bash/Read/Write/Edit/Glob/Grep;
   always enabled, not affected by ``enabled_tools``.
2. **Project tools** — from ``ToolRegistry`` (builtin_tools.py +
   custom/ + enterprise); toggleable via ``enabled_tools``.
3. **MCP tools** — MCP server names from ``McpRegistry``;
   always enabled (individual MCP-tool discovery requires a live
   connection).

Semantics
---------
``enabled_tools == []`` means **all project tools are enabled**.
The first *disable* operation expands ``[]`` to the full project-
tool list minus the disabled tool.  Subsequent toggles are plain
list add / remove.

Only project tools (source=``"project"``) can be toggled; builtins
and MCPs return 400 on PUT/DELETE.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger("bocomadp.agent_tools")

agent_tools_router = APIRouter(
    prefix="/api/agents",
    tags=["agent-tools"],
)

# ------------------------------------------------------------------
# workspace builtins — hardcoded to avoid requiring a live workspace
# ------------------------------------------------------------------

_BUILTIN_TOOLS: list[dict] = [
    {
        "name": "bash",
        "description": (
            "在工作区沙箱中执行bash命令。"
            "命令在工作区目录中运行，可以读写文件、安装包和执行脚本。"
        ),
    },
    {
        "name": "read",
        "description": (
            "读取工作区中文件的内容。"
            "支持为大型文件选择行范围。"
        ),
    },
    {
        "name": "write",
        "description": (
            "向工作区中的文件写入内容。"
            "会自动创建父目录。"
        ),
    },
    {
        "name": "edit",
        "description": (
            "在现有文件中执行精确的字符串替换。"
            "适用于无需重写整个文件的有针对性修改。"
        ),
    },
    {
        "name": "glob",
        "description": (
            "查找匹配glob模式的文件（例如 ``**/*.py``）。"
            "返回相对文件路径。"
        ),
    },
    {
        "name": "grep",
        "description": (
            "使用正则表达式搜索文件内容。"
            "支持基于ripgrep的完整正则语法。"
        ),
    },
]

# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


def _tool_registry(request: Request):
    """Return the global :class:`ToolRegistry` from app state."""
    return request.app.state.tool_registry


def _agent_manager(request: Request):
    """Return the global :class:`MultiAgentManager` from app state."""
    return request.app.state.multi_agent_manager


def _mcp_registry(request: Request):
    """Return the global :class:`McpRegistry` from app state (may be None)."""
    return getattr(request.app.state, "mcp_registry", None)


def _resolve_enabled(all_tool_names: list[str], whitelist: list[str]) -> set[str]:
    """Return the *set* of enabled tool names.

    When *whitelist* is empty every tool is enabled; otherwise only
    names in *whitelist* are active.
    """
    if not whitelist:
        return set(all_tool_names)
    return {n for n in whitelist if n in all_tool_names}


def _all_tool_names(request: Request) -> set[str]:
    """Every known tool name across all sources."""
    names: set[str] = {bt["name"] for bt in _BUILTIN_TOOLS}
    names.update(_tool_registry(request).list_tool_names())
    mcp_reg = _mcp_registry(request)
    if mcp_reg is not None:
        for mcp in mcp_reg.list_mcps():
            name = getattr(mcp, "name", "") or ""
            if name:
                names.add(name)
    return names


# ------------------------------------------------------------------
# GET /api/agents/{agent_id}/tools
# ------------------------------------------------------------------


@agent_tools_router.get(
    "/{agent_id}/tools",
    summary="List tools with per-agent enablement status",
)
async def list_agent_tools(
    agent_id: str,
    request: Request,
) -> dict:
    """Return every tool the agent sees, annotated with its enabled state.

    Tools (builtins + project) are returned in a flat ``tools`` list;
    MCP servers are in a separate ``mcps`` list.

    Response::

        {
          "agent_id": "...",
          "tools": [
            {"name": "bash", "description": "...", "enabled": true, "toggleable": true},
            {"name": "echo", "description": "...", "enabled": false, "toggleable": true}
          ],
          "mcps": [
            {"name": "browser-use", "description": "...", "enabled": true, "toggleable": true}
          ]
        }
    """
    mgr = _agent_manager(request)
    agent = mgr.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    all_names = sorted(_all_tool_names(request))
    enabled_names = _resolve_enabled(all_names, agent.enabled_tools)

    tools: list[dict] = []
    mcps: list[dict] = []

    # 1. Workspace builtins + project tools → merged into `tools`
    for bt in _BUILTIN_TOOLS:
        tools.append({**bt, "enabled": bt["name"] in enabled_names, "toggleable": True})

    for tool in _tool_registry(request).list_tools():
        name = _tool_name(tool)
        tools.append(
            {
                "name": name,
                "description": getattr(tool, "description", "") or "",
                "enabled": name in enabled_names,
                "toggleable": True,
            },
        )

    # 2. MCP servers → separate `mcps` list
    mcp_reg = _mcp_registry(request)
    if mcp_reg is not None:
        for mcp in mcp_reg.list_mcps():
            mcp_name = getattr(mcp, "name", "") or ""
            mcps.append(
                {
                    "name": mcp_name,
                    "description": (
                        getattr(mcp, "description", None)
                        or getattr(
                            getattr(mcp, "mcp_config", None),
                            "url",
                            "",
                        )
                        or ""
                    ),
                    "enabled": mcp_name in enabled_names,
                    "toggleable": True,
                },
            )

    return {
        "agent_id": agent_id,
        "tools": tools,
        "mcps": mcps,
    }


# ------------------------------------------------------------------
# PUT /api/agents/{agent_id}/tools/{tool_name}   — enable
# ------------------------------------------------------------------


@agent_tools_router.put(
    "/{agent_id}/tools/{tool_name}",
    summary="Enable a tool for the agent",
)
async def enable_agent_tool(
    agent_id: str,
    tool_name: str,
    request: Request,
) -> dict:
    """Add *tool_name* to the agent's enabled-tools whitelist."""
    mgr = _agent_manager(request)
    agent = mgr.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    toggleable = _all_tool_names(request)
    if tool_name not in toggleable:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    current = agent.enabled_tools

    # [] means all enabled → already enabled, nothing to do
    if not current:
        agent.enabled_tools = current  # keep []
        logger.info("agent_tools: %s enable %s (already all-enabled)", agent_id, tool_name)
        return {"ok": True}

    if tool_name in current:
        logger.info("agent_tools: %s enable %s (already enabled)", agent_id, tool_name)
        return {"ok": True}

    current.append(tool_name)
    agent.enabled_tools = current
    logger.info("agent_tools: %s enable %s → enabled_tools=%s", agent_id, tool_name, current)
    return {"ok": True}


# ------------------------------------------------------------------
# DELETE /api/agents/{agent_id}/tools/{tool_name}   — disable
# ------------------------------------------------------------------


@agent_tools_router.delete(
    "/{agent_id}/tools/{tool_name}",
    summary="Disable a tool for the agent",
)
async def disable_agent_tool(
    agent_id: str,
    tool_name: str,
    request: Request,
) -> dict:
    """Remove *tool_name* from the agent's enabled-tools whitelist.

    When ``enabled_tools`` is empty (all-enabled), it is first expanded
    to the full tool list so the disable can take effect.
    """
    mgr = _agent_manager(request)
    agent = mgr.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    toggleable = _all_tool_names(request)
    if tool_name not in toggleable:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    current = list(agent.enabled_tools)

    # [] → expand to full list first, then remove
    if not current:
        current = list(toggleable)

    if tool_name not in current:
        logger.info("agent_tools: %s disable %s (already disabled)", agent_id, tool_name)
        return {"ok": True}

    current.remove(tool_name)
    agent.enabled_tools = current
    logger.info("agent_tools: %s disable %s → enabled_tools=%s", agent_id, tool_name, current)
    return {"ok": True}


# ------------------------------------------------------------------
# internal
# ------------------------------------------------------------------


def _tool_name(tool: object) -> str:
    """Best-effort tool name extraction (mirrors ToolRegistry._tool_name)."""
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    fn = getattr(tool, "func", None) or getattr(tool, "_func", None)
    if callable(fn):
        return getattr(fn, "__name__", "") or ""
    return getattr(tool, "__name__", "") or ""


__all__ = ["agent_tools_router"]
