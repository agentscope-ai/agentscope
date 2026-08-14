# -*- coding: utf-8 -*-
"""Agent tool management — query & toggle per-agent tool enablement.

Endpoints
---------
``GET    /agents/{agent_id}/tools``           — list tools with status
``PUT    /agents/{agent_id}/tools/{name}``    — enable a tool
``DELETE /agents/{agent_id}/tools/{name}``    — disable a tool

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

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from agentscope.app.deps import get_current_user_id

logger = logging.getLogger("bocomadp.agent_tools")

agent_tools_router = APIRouter(
    prefix="/agents",
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
# Tool whitelist store
# ------------------------------------------------------------------
# Framework agents (StorageBase) have no ``enabled_tools`` field.
# This dict acts as the write target for tool enable / disable so
# the tool config APIs work for every framework-managed agent.
_tool_whitelists: dict[str, list[str]] = {}


def _whitelist_file() -> Path:
    """Persistent storage path for the tool whitelist store.

    Lives under ``{workspace_dir}/_meta/`` so it survives service
    restarts (the store itself is in-memory and would otherwise be
    lost, silently re-granting every tool to whitelisted agents).
    """
    try:
        from bocomadp.config.uploads_config import get_workspace_dir

        return get_workspace_dir() / "_meta" / "agent_tool_whitelists.json"
    except Exception:  # noqa: BLE001
        return Path(
            os.environ.get(
                "BOCMADP_WHITELIST_FILE",
                os.path.join(
                    os.path.dirname(os.path.dirname(
                        os.path.dirname(os.path.abspath(__file__)))),
                    ".agent_tool_whitelists.json",
                ),
            )
        )


def _persist_whitelists() -> None:
    """Write the whitelist store to disk (best effort)."""
    path = _whitelist_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_tool_whitelists, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        logger.warning("persist tool whitelists failed", exc_info=True)


def load_tool_whitelists() -> None:
    """Restore the whitelist store from disk (called at startup)."""
    path = _whitelist_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("whitelist file %s is not a dict; ignoring", path)
            return
        _tool_whitelists.clear()
        _tool_whitelists.update(data)
        logger.info(
            "loaded %d agent tool whitelists from %s",
            len(data),
            path,
        )
    except FileNotFoundError:
        logger.info("no tool whitelist file yet: %s", path)
    except Exception:  # noqa: BLE001
        logger.warning("load tool whitelists failed", exc_info=True)


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


def _tool_registry(request: Request):
    """Return the global :class:`ToolRegistry` from app state."""
    return request.app.state.tool_registry


def _mcp_registry(request: Request):
    """Return the global :class:`McpRegistry` from app state (may be None)."""
    return getattr(request.app.state, "mcp_registry", None)


async def _resolve_framework_agent(
    request: Request,
    user_id: str,
    agent_id: str,
) -> Any:
    """Look up *agent_id* in framework StorageBase.

    Scoped by the authenticated caller *user_id* so the tool config
    APIs work for every user, not just ``default``.

    Returns:
        The :class:`AgentRecord` if found, ``None`` otherwise.
    """
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        return None
    try:
        return await storage.get_agent(user_id, agent_id)
    except Exception:
        logger.debug(
            "agent_tools: storage lookup failed for %s",
            agent_id,
            exc_info=True,
        )
        return None


def _get_enabled_tools(agent_id: str) -> list[str]:
    """Return the current enabled-tools list for *agent_id*."""
    return list(_tool_whitelists.get(agent_id, []))


def _set_enabled_tools(agent_id: str, tools: list[str]) -> None:
    """Persist *tools* for *agent_id*."""
    _tool_whitelists[agent_id] = tools
    _persist_whitelists()


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
# GET /agents/{agent_id}/tools
# ------------------------------------------------------------------


@agent_tools_router.get(
    "/{agent_id}/tools",
    summary="List tools with per-agent enablement status",
)
async def list_agent_tools(
    agent_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
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
    agent = await _resolve_framework_agent(request, user_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    all_names = sorted(_all_tool_names(request))
    enabled_tools = _get_enabled_tools(agent_id)
    enabled_names = _resolve_enabled(all_names, enabled_tools)

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
# PUT /agents/{agent_id}/tools/{tool_name}   — enable
# ------------------------------------------------------------------


@agent_tools_router.put(
    "/{agent_id}/tools/{tool_name}",
    summary="Enable a tool for the agent",
)
async def enable_agent_tool(
    agent_id: str,
    tool_name: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Add *tool_name* to the agent's enabled-tools whitelist."""
    agent = await _resolve_framework_agent(request, user_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    toggleable = _all_tool_names(request)
    if tool_name not in toggleable:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    current = _get_enabled_tools(agent_id)

    # [] means all enabled → already enabled, nothing to do
    if not current:
        _set_enabled_tools(agent_id, current)  # keep []
        logger.info("agent_tools: %s enable %s (already all-enabled)", agent_id, tool_name)
        return {"ok": True}

    if tool_name in current:
        logger.info("agent_tools: %s enable %s (already enabled)", agent_id, tool_name)
        return {"ok": True}

    current.append(tool_name)
    _set_enabled_tools(agent_id, current)
    logger.info("agent_tools: %s enable %s → enabled_tools=%s", agent_id, tool_name, current)
    return {"ok": True}


# ------------------------------------------------------------------
# DELETE /agents/{agent_id}/tools/{tool_name}   — disable
# ------------------------------------------------------------------


@agent_tools_router.delete(
    "/{agent_id}/tools/{tool_name}",
    summary="Disable a tool for the agent",
)
async def disable_agent_tool(
    agent_id: str,
    tool_name: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Remove *tool_name* from the agent's enabled-tools whitelist.

    When ``enabled_tools`` is empty (all-enabled), it is first expanded
    to the full tool list so the disable can take effect.
    """
    agent = await _resolve_framework_agent(request, user_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    toggleable = _all_tool_names(request)
    if tool_name not in toggleable:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    current = _get_enabled_tools(agent_id)

    # [] → expand to full list first, then remove
    if not current:
        current = list(toggleable)

    if tool_name not in current:
        logger.info("agent_tools: %s disable %s (already disabled)", agent_id, tool_name)
        return {"ok": True}

    current.remove(tool_name)
    _set_enabled_tools(agent_id, current)
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


__all__ = [
    "agent_tools_router",
    "_tool_whitelists",
    "_get_enabled_tools",
    "_set_enabled_tools",
    "load_tool_whitelists",
]
