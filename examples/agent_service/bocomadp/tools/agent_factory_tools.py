# -*- coding: utf-8 -*-
"""Agent factory tools — used by the agent-creator to manage agent configs.

These tools call the framework's built-in ``/agent`` REST API
(StorageBase → Postgres) so every agent the agent-creator produces
is persisted, user-scoped, and visible to the framework's native
endpoints.

Tool list:

- ``create_agent``          — create a new agent (id auto-generated)
- ``update_agent``          — update an existing agent
- ``delete_agent``          — delete an agent
- ``list_agents``           — list current user's agents
- ``get_agent``             — get one agent's full config
- ``list_tools_for_agent``  — list all available tools + MCPs
"""

from __future__ import annotations

import contextvars
import json
import logging
import urllib.parse
from typing import Any

import httpx

logger = logging.getLogger("bocomadp.agent_factory_tools")

try:
    from agentscope.tool import FunctionTool
except ImportError:
    FunctionTool = None  # type: ignore[assignment]


def tool(fn=None, **opts):
    """@tool 装饰器：把工具函数包装为框架 FunctionTool（ToolBase）。

    新版 agentscope 不再提供 ``agentscope.tool.tool`` 装饰器；直接
    用 FunctionTool 包装，保证注入 Toolkit 的对象是 ToolBase 实例
    （否则框架 remove_tool 访问 tool.name 时会对裸函数抛
    AttributeError）。agentscope 未安装时（仅静态检查场景）原样返回。
    """
    def _wrap(f):
        if FunctionTool is None:
            return f
        return FunctionTool(f, **opts)

    if fn is not None:
        return _wrap(fn)
    return _wrap


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_tool_registry: Any = None
_mcp_registry: Any = None

# Shared user-id context — set by ``build_agent_tools`` (main.py) on each
# chat run so factory tools know which user is calling.
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_factory_user_id", default="default",
)

# Shared guwp token context — set by ``TokenCaptureMiddleware`` (main.py)
# from the ``guwpToken`` request header.  The framework's ChatRunRegistry
# spawns the chat run via ``asyncio.create_task``, which copies the current
# context, so the token is visible to factory tools inside the run.
_current_token: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_factory_token", default="",
)

# Current agent-creator session id — set by ``build_agent_tools`` (main.py)
# on each chat run. Available to factory tools that need session context.
_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_factory_session_id", default="",
)

# Framework agent API base (same process, localhost is safe).
_AGENT_API = "http://localhost:8000/agent"

# Tool-config API base — the per-agent tool whitelist endpoints.
_TOOLS_API = "http://localhost:8000/agents"

# Session API base — used to ensure a target agent has a session before
# skill operations (skill endpoints resolve the workspace via session).
_SESSIONS_API = "http://localhost:8000/sessions"

# Skill API base — external skillhub catalog + download endpoints.
_SKILLS_API = "http://localhost:8000/workspace"

# Workspace builtins are always available and not affected by
# ``enabled_tools`` — skip them when aligning tool whitelists.
_BUILTIN_NAMES = {"bash", "read", "write", "edit", "glob", "grep"}

#: Unicode 连字符/空白 → ASCII 映射（LLM 生成的名称中很常见）。
_NAME_TRANS = str.maketrans(
    {
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2015": "-",  # horizontal bar
        "\u2212": "-",  # minus sign
        "\u00a0": " ",  # non-breaking space
    },
)


def _clean_name(name: str) -> str:
    """清洗智能体名称：Unicode 连字符/空白归一化为 ASCII。

    LLM 生成的名称常含 U+2011（不间断连字符）等字符；这些字符一旦
    被拼进 agent_id、目录名或 K8s label，会触发 API server 422。
    归一化只影响显示名称中的特殊连字符，可读性不变。
    """
    return name.translate(_NAME_TRANS).strip()


def init_factory_tools(
    tool_registry: Any = None,
    mcp_registry: Any = None,
) -> None:
    """Wire the factory tools to live registries.

    Call once at startup, before any agent-creator conversation.

    Args:
        tool_registry: :class:`ToolRegistry` instance.
        mcp_registry: :class:`McpRegistry` instance.
    """
    global _tool_registry, _mcp_registry  # noqa: PLW0603
    _tool_registry = tool_registry
    _mcp_registry = mcp_registry
    logger.info(
        "agent_factory_tools initialized: tools=%d mcps=%d",
        len(_tool_registry.list_tool_names()) if _tool_registry else 0,
        len(_mcp_registry.list_mcps()) if _mcp_registry else 0,
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _api(
    method: str,
    path: str,
    body: dict | None = None,
    base: str = _AGENT_API,
) -> dict | str:
    """Call a framework REST API and return parsed JSON or an error
    string.

    Async on purpose: factory tools run inside the uvicorn event loop,
    and a synchronous ``urllib`` call to this same server deadlocks —
    the loop cannot serve the request until the tool returns, but the
    tool waits for the response (10s timeout, then the agent is still
    created and the whitelist write is silently skipped).
    """
    url = f"{base}{path}"
    headers = {
        "Content-Type": "application/json",
        "X-User-ID": _current_user_id.get(),
        "guwpToken": _current_token.get(),
    }
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.request(
                method,
                url,
                json=body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        return f"无法连接 Agent API: {exc}"

    if resp.status_code == 204:  # DELETE returns no content
        return {}
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        return f"请求失败 (HTTP {resp.status_code}): {detail}"
    return resp.json()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def create_agent(
    name: str,
    system_prompt: str,
    max_iters: int = 20,
    enabled_tools: list[str] = [],
) -> str:
    """创建一个新的智能体配置。

    智能体 ID 由系统自动生成（返回结果中会包含）。创建成功后返回完整
    配置信息。

    Args:
        name (str): 显示名称，如 '客服助手'
        system_prompt (str): 决定智能体行为的核心提示词
        max_iters (int): 最大推理轮次（默认20，复杂任务可设30~50）
        enabled_tools (list[str]): 要启用的工具名列表；空列表表示全部可用。
            工具名从 list_tools_for_agent 的结果中选取。
    """
    name = _clean_name(name)
    body: dict = {
        "name": name,
        "system_prompt": system_prompt,
        "react_config": {"max_iters": max_iters},
    }
    result = await _api("POST", "/", body)
    if isinstance(result, str):
        return result
    agent_id = result.get("agent_id", "")
    if not agent_id:
        return json.dumps(result, ensure_ascii=False, indent=2)

    # Tool whitelist — build the whitelist directly for the requested
    # tools. The PUT endpoint's semantics are「empty list = all enabled」,
    # so per-tool PUT calls are no-ops on a fresh agent; writing the
    # whitelist directly makes a non-empty ``enabled_tools`` actually
    # restrict the agent to exactly those tools at runtime.
    errors: list[str] = []
    if enabled_tools:
        from bocomadp.routers.agent_tools import _set_enabled_tools

        _set_enabled_tools(agent_id, list(enabled_tools))

    lines = [f"智能体 '{name}' 创建成功，agent_id: {agent_id}"]
    if enabled_tools:
        lines.append(f"已启用工具: {', '.join(enabled_tools)}")
    else:
        lines.append("工具配置: 全部可用")
    if errors:
        lines.append("部分工具启用失败:\n" + "\n".join(errors))
    return "\n".join(lines)


@tool
async def update_agent(
    agent_id: str,
    name: str = "",
    system_prompt: str = "",
    max_iters: int | None = None,
) -> str:
    """修改已有智能体的配置。未传入的字段保持原值不变。

    先调用 get_agent 查看当前配置，再决定修改哪些字段。

    Args:
        agent_id (str): 要修改的智能体 ID（系统生成的 UUID）
        name (str): 新的显示名称（空字符串表示不改）
        system_prompt (str): 新的系统提示词（空字符串表示不改）
        max_iters (int | None): 新的最大轮次（None表示不改）
    """
    body: dict = {}
    if name:
        body["name"] = _clean_name(name)
    if system_prompt:
        body["system_prompt"] = system_prompt
    if max_iters is not None:
        body["react_config"] = {"max_iters": max_iters}

    if not body:
        return "未提供任何要修改的字段。"

    result = await _api("PATCH", f"/{agent_id}", body)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
async def delete_agent(agent_id: str) -> str:
    """删除一个智能体配置。系统内置的智能体不可删除。

    Args:
        agent_id (str): 要删除的智能体 ID（系统生成的 UUID）
    """
    if agent_id.startswith("_"):
        return f"智能体 '{agent_id}' 是系统内置的，不可删除。"

    result = await _api("DELETE", f"/{agent_id}")
    if isinstance(result, str):
        return result
    return f"智能体 '{agent_id}' 已删除。"


@tool
async def list_agents() -> str:
    """列出当前用户创建的所有智能体的摘要信息。"""
    result = await _api("GET", "/")
    if isinstance(result, str):
        return result

    agents = result.get("agents", [])
    if not agents:
        return "当前还没有创建任何智能体。调用 create_agent 来创建第一个吧。"

    lines = [f"共 {len(agents)} 个智能体:\n"]
    for a in agents:
        agent_id = a.get("agent_id", "")
        data = a.get("data", {})
        name = data.get("name", "")
        sp = (data.get("system_prompt", "") or "")[:60]
        max_iters = (
            data.get("react_config", {}).get("max_iters", 20)
        )
        editable = "✓" if a.get("editable") else "✗"
        lines.append(
            f"- {agent_id}  {name}\n"
            f"  可编辑={editable}  max_iters={max_iters}  "
            f"prompt={sp}{'...' if len(sp) >= 60 else ''}",
        )
    return "\n".join(lines)


@tool
async def get_agent(agent_id: str) -> str:
    """查看指定智能体的完整配置，包括 system prompt、max_iters 等。

    Args:
        agent_id (str): 智能体 ID（系统生成的 UUID）
    """
    # Framework has no single-agent GET — list all and filter.
    result = await _api("GET", "/")
    if isinstance(result, str):
        return result

    for a in result.get("agents", []):
        if a.get("agent_id") == agent_id:
            return json.dumps(a, ensure_ascii=False, indent=2)

    return (
        f"智能体 '{agent_id}' 不存在。"
        f"调用 list_agents 查看所有已创建的智能体。"
    )


@tool
def list_tools_for_agent() -> str:
    """列出系统中所有可分配给智能体的工具和MCP服务器。

    返回两部分：
    - tools: 项目工具和框架内置工具的名称+描述
    - mcps: MCP 服务器列表
    """
    tools_info: list[str] = []
    mcps_info: list[str] = []

    # Builtin tools
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
                mcps_info.append(
                    f"- {mcp_name}: {mcp_desc}"
                    if mcp_desc
                    else f"- {mcp_name}",
                )

    parts = ["# 系统可用工具一览", "\n".join(tools_info)]
    if mcps_info:
        parts.append("\n".join(mcps_info))

    return "\n".join(parts)


@tool
async def set_agent_tools(
    agent_id: str,
    enabled_tools: list[str],
) -> str:
    """全量设置智能体的工具白名单（覆盖式）。

    - enabled_tools 为空列表：全部工具可用
    - enabled_tools 非空：只启用列表中的工具（按名称精确匹配）

    内置工具（bash/read/write/edit/glob/grep）始终可用，不受影响。

    Args:
        agent_id (str): 目标智能体 ID
        enabled_tools (list[str]): 工具名列表（从 list_tools_for_agent 选取）
    """
    # 1. Read current enabled state
    result = await _api("GET", f"/{agent_id}/tools", base=_TOOLS_API)
    if isinstance(result, str):
        return result

    current_enabled: set[str] = set()
    for t in result.get("tools", []):
        if t.get("name") not in _BUILTIN_NAMES and t.get("enabled"):
            current_enabled.add(t["name"])
    for m in result.get("mcps", []):
        if m.get("enabled"):
            current_enabled.add(m["name"])

    target = set(enabled_tools)
    errors: list[str] = []

    # 2. Diff-align. Empty target → all-enabled: every currently
    # enabled name is disabled one by one (last removal lands on the
    # all-enabled [] state).
    if not target:
        disabled = [
            t["name"]
            for t in result.get("tools", [])
            if t.get("name") not in _BUILTIN_NAMES and not t.get("enabled")
        ]
        if not disabled:
            return f"智能体 '{agent_id}' 的工具已是全部可用。"
        for name in sorted(current_enabled):
            r = await _api("DELETE", f"/{agent_id}/tools/{name}", base=_TOOLS_API)
            if isinstance(r, str):
                errors.append(r)
    else:
        for name in sorted(current_enabled - target):
            r = await _api("DELETE", f"/{agent_id}/tools/{name}", base=_TOOLS_API)
            if isinstance(r, str):
                errors.append(r)
        for name in sorted(target - current_enabled):
            r = await _api("PUT", f"/{agent_id}/tools/{name}", base=_TOOLS_API)
            if isinstance(r, str):
                errors.append(r)

    if errors:
        return "工具配置部分失败:\n" + "\n".join(errors)
    if not target:
        return f"智能体 '{agent_id}' 的工具已设置为全部可用。"
    return (
        f"智能体 '{agent_id}' 的工具白名单已设置为: "
        f"{', '.join(sorted(target))}。"
    )


async def _get_or_create_session(agent_id: str) -> tuple[str, str]:
    """Ensure *agent_id* has at least one session.

    Skill endpoints resolve the target workspace through a session
    record, so a session must exist before any skill operation.

    Returns:
        ``(session_id, error)`` — exactly one of the two is non-empty.
    """
    # 注意尾斜杠：框架路由定义 GET "/"（/sessions/），请求无尾斜杠的
    # /sessions 会触发 307 重定向；虽然 _api 已开启 follow_redirects，
    # 这里仍显式带上尾斜杠，避免依赖重定向语义。
    result = await _api(
        "GET",
        f"/?agent_id={urllib.parse.quote(agent_id)}",
        base=_SESSIONS_API,
    )
    if isinstance(result, str):
        return "", result

    sessions = result.get("sessions", [])
    if sessions:
        session = sessions[0].get("session", {}) or {}
        sid = session.get("id", "")
        if sid:
            return sid, ""

    created = await _api("POST", "/", {"agent_id": agent_id}, base=_SESSIONS_API)
    if isinstance(created, str):
        return "", created
    return created.get("session_id", ""), ""


@tool
async def list_available_skills(agent_id: str, keyword: str = "") -> str:
    """查看技能市场中可用的技能列表。

    Args:
        agent_id (str): 目标智能体 ID
        keyword (str): 可选关键词，按技能名/描述过滤
    """
    session_id, err = await _get_or_create_session(agent_id)
    if err:
        return err

    params = urllib.parse.urlencode({
        "agent_id": agent_id,
        "session_id": session_id,
        "q": keyword,
    })
    result = await _api("GET", f"/skills/external?{params}", base=_SKILLS_API)
    if isinstance(result, str):
        return result

    skills = result.get("skills", [])
    if not skills:
        return "技能市场暂无可用技能。"

    lines = [f"共 {len(skills)} 个技能（used=已安装）:\n"]
    for s in skills:
        name = s.get("name", "")
        category = s.get("category", "")
        desc = (s.get("description", "") or "")[:60]
        used = "✓已安装" if s.get("used") else "未安装"
        lines.append(f"- {category}:{name}  [{used}]  {desc}")
    return "\n".join(lines)


@tool
async def enable_skill_for_agent(agent_id: str, skill_full_name: str) -> str:
    """为智能体安装（启用）一个技能。

    Args:
        agent_id (str): 目标智能体 ID
        skill_full_name (str): 技能全名，格式 'category:name'
            （如 'public:writing'），从 list_available_skills 的结果中选取
    """
    session_id, err = await _get_or_create_session(agent_id)
    if err:
        return err

    params = urllib.parse.urlencode({
        "agent_id": agent_id,
        "session_id": session_id,
    })
    result = await _api(
        "POST",
        f"/skill/download/{urllib.parse.quote(skill_full_name, safe=':')}"
        f"?{params}",
        base=_SKILLS_API,
    )
    if isinstance(result, str):
        return result
    if result.get("success"):
        return f"技能 '{skill_full_name}' 已安装到智能体 '{agent_id}'。"
    return json.dumps(result, ensure_ascii=False, indent=2)


__all__ = [
    "init_factory_tools",
    "_current_user_id",
    "_current_token",
    "create_agent",
    "update_agent",
    "delete_agent",
    "list_agents",
    "get_agent",
    "list_tools_for_agent",
    "set_agent_tools",
    "list_available_skills",
    "enable_skill_for_agent",
    "_current_session_id",
]
