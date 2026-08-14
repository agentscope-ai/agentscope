# -*- coding: utf-8 -*-
"""Per-agent tool whitelist applied to workspace MCPs.

The framework injects MCPs straight from ``workspace.list_mcps()``
(see ``agentscope.app._service._toolkit.get_toolkit``), bypassing
``extra_agent_tools`` entirely — so the whitelist maintained by
``agent_tools_router`` (PUT/DELETE ``/agents/{id}/tools/{name}``)
cannot filter MCPs at the ``extra_factory`` layer the way it filters
project tools.

Fix without touching framework code: wrap the workspace manager.
``WorkspaceManagerBase.get_workspace`` already receives ``agent_id``,
so the wrapper intercepts it and returns a delegating proxy whose
``list_mcps`` applies the per-agent whitelist.  ``get_toolkit`` calls
``list_mcps`` on every chat run, so whitelist changes take effect
immediately.
"""

from __future__ import annotations

from typing import Any

from agentscope.workspace import WorkspaceBase


class _WhitelistWorkspaceProxy(WorkspaceBase):
    """Delegating workspace proxy filtering ``list_mcps`` per agent.

    必须继承 :class:`WorkspaceBase`：框架 ``Agent._get_system_prompt``
    用 ``isinstance(self.offloader, WorkspaceBase)`` 判断是否把工作区
    instructions（workspace 提示词）追加进系统提示词——普通委托类不满足
    该检查，会导致 workspace 提示词被静默丢弃。抽象方法全部委托给
    真实工作区，行为不变。
    """

    def __init__(self, workspace: Any, agent_id: str) -> None:
        # ``object.__setattr__`` keeps ``__setattr__`` default so the
        # proxy stays inert; ``__getattr__`` below only fires on miss.
        # 不调用 ``super().__init__``：避免在代理上生成 workspace_id /
        # workdir 等真实属性，使这些属性仍经 ``__getattr__`` 委托。
        object.__setattr__(self, "_workspace", workspace)
        object.__setattr__(self, "_agent_id", agent_id)

    # ── WorkspaceBase 抽象方法：全部委托给真实工作区 ────────────

    async def initialize(self) -> None:
        return await self._workspace.initialize()

    async def close(self) -> None:
        return await self._workspace.close()

    def get_backend(self) -> Any:
        return self._workspace.get_backend()

    async def get_instructions(self) -> str:
        return await self._workspace.get_instructions()

    async def add_mcp(self, mcp_client: Any) -> None:
        return await self._workspace.add_mcp(mcp_client)

    async def remove_mcp(self, name: str) -> None:
        return await self._workspace.remove_mcp(name)

    # ── WorkspaceBase 已实现方法：显式转发，保持与真实工作区一致 ──
    # 继承后这些方法默认走基类实现，会绕开 LocalWorkspace 等子类
    # 的覆写（如 hash 索引的 list_skills、PowerShell 的 list_tools），
    # 因此逐一转发到被代理的工作区。

    async def reset(self) -> None:
        return await self._workspace.reset()

    async def list_tools(self) -> list:
        return await self._workspace.list_tools()

    async def list_skills(self) -> list:
        return await self._workspace.list_skills()

    async def add_skill(self, skill_path: str) -> None:
        return await self._workspace.add_skill(skill_path)

    async def add_skill_archive(self, *args: Any, **kwargs: Any) -> None:
        return await self._workspace.add_skill_archive(*args, **kwargs)

    async def remove_skill(self, name: str) -> None:
        return await self._workspace.remove_skill(name)

    async def offload_context(self, *args: Any, **kwargs: Any) -> Any:
        return await self._workspace.offload_context(*args, **kwargs)

    async def offload_tool_result(self, *args: Any, **kwargs: Any) -> Any:
        return await self._workspace.offload_tool_result(*args, **kwargs)

    # ── 代理特有：按智能体白名单过滤 MCP ────────────────────────

    async def list_mcps(self) -> list:
        """Return MCPs allowed by the per-agent tool whitelist.

        Empty whitelist means all available (same semantics as the
        tool config APIs); non-empty keeps only listed names.
        """
        from bocomadp.routers.agent_tools import _tool_whitelists

        mcps = await self._workspace.list_mcps()
        whitelist = _tool_whitelists.get(self._agent_id, [])
        if not whitelist:
            return mcps
        allowed = set(whitelist)
        return [m for m in mcps if getattr(m, "name", "") in allowed]

    def __getattr__(self, item: str) -> Any:
        # Everything else (list_tools / list_skills / get_backend /
        # add_mcp / add_skill_archive / workdir / ...) delegates.
        return getattr(self._workspace, item)


class WhitelistWorkspaceManager:
    """Wrap a ``WorkspaceManagerBase``, filtering MCPs per agent.

    All methods and attributes except ``get_workspace`` and the
    lifecycle hooks delegate to the inner manager, so local and K8s
    managers both work unchanged.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> Any:
        ws = await self._inner.get_workspace(
            user_id,
            agent_id,
            session_id,
            workspace_id,
        )
        return _WhitelistWorkspaceProxy(ws, agent_id)

    async def __aenter__(self) -> "WhitelistWorkspaceManager":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> Any:
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


__all__ = ["WhitelistWorkspaceManager"]
