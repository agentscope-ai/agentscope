# -*- coding: utf-8 -*-
"""Tool registry — manages custom tools for agent injection.

The :class:`ToolRegistry` holds a list of tool functions (decorated
with ``@tool`` from agentscope) that :class:`AgentBuilder` injects
into the agent's :class:`Toolkit` at build time.

Usage::

    from bocomadp.tools import ToolRegistry

    registry = ToolRegistry()
    registry.load_builtin_tools()   # loads tools from builtin_tools.py
    registry.register(my_custom_tool)  # add your own

    # Later, in AgentBuilder.build():
    tools = registry.list_tools()
    toolkit = Toolkit(tools=tools)
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of custom tools for agent injection."""

    def __init__(self) -> None:
        self._tools: list[Any] = []
        self._tool_names: set[str] = set()

    def register(self, tool: Any) -> None:
        """Register a tool. Idempotent — duplicate names are skipped."""
        name = self._tool_name(tool)
        if name in self._tool_names:
            logger.debug("tool already registered: %s", name)
            return
        self._tools.append(tool)
        self._tool_names.add(name)
        logger.info("tool registered: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a tool by name."""
        self._tools = [
            t for t in self._tools if self._tool_name(t) != name
        ]
        self._tool_names.discard(name)

    def list_tools(self) -> list[Any]:
        """Return all registered tools (for Toolkit construction)."""
        return list(self._tools)

    def list_tool_names(self) -> list[str]:
        """Return all registered tool names."""
        return sorted(self._tool_names)

    def load_builtin_tools(self) -> None:
        """加载 builtin_tools.py 中所有 ``@tool`` 装饰的函数。

        在该模块顶部用 ``@tool`` 装饰函数即可，重启后自动注册。
        """
        try:
            from . import builtin_tools  # noqa: F401
            self._scan_module_for_tools(builtin_tools)
        except ImportError:
            logger.warning("builtin_tools module not found")
        except Exception:
            logger.exception("failed to load builtin tools")

    def load_custom_tools(self) -> None:
        """自动扫描 ``custom/`` 包下所有子模块的 ``@tool`` 函数。

        在 ``custom/`` 下新建任意 ``.py`` 文件，用 ``@tool`` 装饰函数，
        重启后自动注册，无需修改 main.py。
        """
        try:
            import importlib
            import pkgutil
            from . import custom as _custom_pkg
        except ImportError:
            logger.debug("custom tools package not found; skipping")
            return
        for _finder, modname, _ispkg in pkgutil.walk_packages(
            _custom_pkg.__path__,
            prefix=_custom_pkg.__name__ + ".",
        ):
            try:
                mod = importlib.import_module(modname)
            except Exception:
                logger.warning(
                    "failed to import custom tool module: %s",
                    modname,
                    exc_info=True,
                )
                continue
            self._scan_module_for_tools(mod)

    def _scan_module_for_tools(self, mod: Any) -> None:
        """扫描模块命名空间，注册所有 ``_is_tool`` 标记的可调用对象。"""
        for name in dir(mod):
            obj = getattr(mod, name)
            if callable(obj) and getattr(obj, "_is_tool", False):
                # 复用实例方法 register（幂等）
                self.register(obj)

    @staticmethod
    def _tool_name(tool: Any) -> str:
        """Best-effort tool name extraction."""
        name = getattr(tool, "name", None)
        if isinstance(name, str) and name:
            return name
        fn = getattr(tool, "func", None) or getattr(tool, "_func", None)
        if callable(fn):
            return getattr(fn, "__name__", "") or ""
        return getattr(tool, "__name__", "") or ""


__all__ = ["ToolRegistry"]
