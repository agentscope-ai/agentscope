# -*- coding: utf-8 -*-
"""MCP 注册表 —— 管理 MCPClient 实例，支持 custom/ 自动扫描。

与 ToolRegistry / MiddlewareRegistry 保持一致的自动发现模式：

- :meth:`load_builtin`   扫描 ``builtin_mcps.py`` 的模块级 MCPClient 实例
- :meth:`load_custom`    扫描 ``custom/`` 包下所有子模块的 MCPClient 实例

判定标记：``_is_mcp_client = True``（打在 MCPClient 类或实例上均可）。

用法::

    from bocomadp.mcp import McpRegistry
    registry = McpRegistry()
    registry.load_builtin()
    registry.load_custom()
    mcps = registry.list_mcps()   # 传给 LocalWorkspaceManager(default_mcps=...)

## 在 custom/ 下加一个 MCP

1. 在 ``custom/`` 下建 ``amap.py``：
2. 构造 MCPClient 实例并导出::

       from agentscope.mcp import MCPClient, HttpMCPConfig
       amap = MCPClient(
           name="amap",
           mcp_config=HttpMCPConfig(url="https://mcp.amap.com/mcp?key=xxx"),
           is_stateful=False,
       )

3. 重启即生效，无需改 main.py。

## 标记机制

agentscope 的 MCPClient 不自带 ``_is_mcp_client`` 标记，本注册表在
:func:`load_builtin` / :func:`load_custom` 内部对扫描到的对象打标记，
因此用户只需 ``mcp = MCPClient(...)`` 导出实例即可，无需装饰器。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# duck-type 判定：MCPClient 有 name + mcp_config 属性，且不是类。
# 避免硬依赖 agentscope.mcp（语法检查环境可能没有）。


def _looks_like_mcp_client(obj: Any) -> bool:
    """判定对象是否为 MCPClient 实例（duck-type）。"""
    if isinstance(obj, type):
        return False
    return hasattr(obj, "name") and hasattr(obj, "mcp_config")


class McpRegistry:
    """MCPClient 注册表。"""

    def __init__(self) -> None:
        self._mcps: list[Any] = []
        self._names: set[str] = set()

    def register(self, mcp: Any) -> None:
        """注册一个 MCPClient 实例。幂等 —— 同名跳过。"""
        name = getattr(mcp, "name", None) or repr(mcp)
        if name in self._names:
            logger.debug("mcp already registered: %s", name)
            return
        # 打标记，便于外部按属性筛选
        try:
            mcp._is_mcp_client = True  # type: ignore[attr-defined]
        except Exception:
            pass
        self._mcps.append(mcp)
        self._names.add(name)
        logger.info("mcp registered: %s", name)

    def unregister(self, name: str) -> None:
        """按 name 移除一个 MCP。"""
        self._mcps = [m for m in self._mcps if getattr(m, "name", None) != name]
        self._names.discard(name)

    def list_mcps(self) -> list[Any]:
        """返回所有已注册的 MCPClient 实例。"""
        return list(self._mcps)

    def list_mcp_names(self) -> list[str]:
        """返回所有已注册的 MCP 名称。"""
        return sorted(self._names)

    def load_builtin(self) -> None:
        """扫描 builtin_mcps.py 的模块级 MCPClient 实例。"""
        try:
            from . import builtin_mcps  # noqa: F401
            self._scan_module(builtin_mcps)
        except ImportError:
            logger.debug("builtin_mcps module not found; skipping")
        except Exception:
            logger.exception("failed to load builtin mcps")

    def load_custom(self) -> None:
        """自动扫描 ``custom/`` 包下所有子模块的 MCPClient 实例。"""
        try:
            import importlib
            import pkgutil
            from . import custom as _custom_pkg
        except ImportError:
            logger.debug("custom mcps package not found; skipping")
            return
        for _finder, modname, _ispkg in pkgutil.walk_packages(
            _custom_pkg.__path__,
            prefix=_custom_pkg.__name__ + ".",
        ):
            try:
                mod = importlib.import_module(modname)
            except Exception:
                logger.warning(
                    "failed to import custom mcp module: %s",
                    modname,
                    exc_info=True,
                )
                continue
            self._scan_module(mod)

    def _scan_module(self, mod: Any) -> None:
        """扫描模块命名空间，注册所有 MCPClient 实例。"""
        for name in dir(mod):
            obj = getattr(mod, name)
            if _looks_like_mcp_client(obj):
                self.register(obj)


__all__ = ["McpRegistry"]
