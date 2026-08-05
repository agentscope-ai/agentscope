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
        """Load tools from :mod:`builtin_tools`.

        Importing this module registers every ``@tool``-decorated
        function. Add new tools to ``builtin_tools.py``.
        """
        try:
            from . import builtin_tools  # noqa: F401
            for name in dir(builtin_tools):
                obj = getattr(builtin_tools, name)
                if callable(obj) and getattr(obj, "_is_tool", False):
                    self.register(obj)
        except ImportError:
            logger.warning("builtin_tools module not found")
        except Exception:
            logger.exception("failed to load builtin tools")

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
