# -*- coding: utf-8 -*-
"""WhitelistWorkspaceManager 代理回归测试。

回归背景：``_WhitelistWorkspaceProxy`` 早期是普通类，不是
``agentscope.workspace.WorkspaceBase`` 子类。框架
``Agent._get_system_prompt`` 用 ``isinstance(self.offloader, WorkspaceBase)``
决定是否把工作区 instructions（workspace 提示词）追加进系统提示词；
代理不满足该 isinstance 检查 → 工作区提示词被静默丢弃，
表现为「skill 的提示词还在，workspace 的提示词不见了」。

风格对齐 bocomadp 现有 tests/（pytest + asyncio.run，无 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentscope.workspace import WorkspaceBase

from bocomadp.routers.agent_tools import _tool_whitelists
from bocomadp.workspace.whitelist import WhitelistWorkspaceManager


class FakeWorkspace:
    """最小 workspace 桩：满足代理需要委托的接口即可。"""

    def __init__(self) -> None:
        self.workdir = "/fake/workdir"
        self.workspace_id = "fake-ws"
        self._mcps = [
            SimpleNamespace(name="allowed-mcp"),
            SimpleNamespace(name="blocked-mcp"),
        ]

    async def get_instructions(self) -> str:
        return "workspace instructions here"

    async def list_mcps(self):
        return list(self._mcps)

    async def list_skills(self):
        return []

    async def list_tools(self):
        return []

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def get_backend(self):
        raise NotImplementedError

    async def add_mcp(self, mcp_client) -> None:
        return None

    async def remove_mcp(self, name: str) -> None:
        return None


class FakeManager:
    """最小 workspace manager 桩。"""

    def __init__(self, workspace: FakeWorkspace) -> None:
        self._workspace = workspace

    async def get_workspace(self, user_id, agent_id, session_id, workspace_id=None):
        return self._workspace

    async def __aenter__(self) -> "FakeManager":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


def _get_proxy(agent_id: str = "agent-1"):
    manager = WhitelistWorkspaceManager(FakeManager(FakeWorkspace()))

    async def _resolve():
        return await manager.get_workspace("u", agent_id, "s")

    return asyncio.run(_resolve())


def test_proxy_is_workspace_base() -> None:
    """代理必须是 WorkspaceBase 子类，否则框架丢弃 workspace 提示词。

    回归点：Agent._get_system_prompt 中
    ``isinstance(self.offloader, WorkspaceBase)`` 判断。
    """
    proxy = _get_proxy()
    assert isinstance(proxy, WorkspaceBase)


def test_proxy_delegates_workspace_instructions() -> None:
    """get_instructions 委托给真实工作区（workspace 提示词来源）。"""
    proxy = _get_proxy()
    assert asyncio.run(proxy.get_instructions()) == "workspace instructions here"


def test_proxy_delegates_attributes_and_methods() -> None:
    """其余属性/方法（workdir、list_skills、list_tools…）继续委托。"""
    proxy = _get_proxy()
    assert proxy.workdir == "/fake/workdir"
    assert proxy.workspace_id == "fake-ws"
    assert asyncio.run(proxy.list_skills()) == []


def test_proxy_filters_mcps_by_whitelist() -> None:
    """空白名单=全部放行；非空白名单只保留列出的 MCP（原有语义）。"""
    _tool_whitelists.pop("agent-1", None)
    proxy = _get_proxy("agent-1")
    mcps = asyncio.run(proxy.list_mcps())
    assert [m.name for m in mcps] == ["allowed-mcp", "blocked-mcp"]

    _tool_whitelists["agent-1"] = ["allowed-mcp"]
    try:
        proxy = _get_proxy("agent-1")
        mcps = asyncio.run(proxy.list_mcps())
        assert [m.name for m in mcps] == ["allowed-mcp"]
    finally:
        _tool_whitelists.pop("agent-1", None)


def test_manager_delegates_lifecycle() -> None:
    """Manager 的 async 上下文与其余方法仍委托给内层管理器。"""
    inner = FakeManager(FakeWorkspace())
    manager = WhitelistWorkspaceManager(inner)

    async def _lifecycle():
        async with manager:
            return True

    assert asyncio.run(_lifecycle())
    assert manager._inner is inner
