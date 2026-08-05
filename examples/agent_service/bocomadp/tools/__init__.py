"""Custom tool registry.

Provides :class:`ToolRegistry` — a registry of custom tools that
can be injected into the agent's :class:`Toolkit` at build time.

## How to add a new tool

1. Create a function in this package (see ``builtin_tools.py`` for
   examples) decorated with ``@tool`` from agentscope.
2. Register it in :meth:`ToolRegistry.load_builtin_tools` or call
   ``registry.register(my_tool)`` at startup.
3. The tool will automatically appear in every agent's toolkit.

## Directory layout

- ``registry.py``    — :class:`ToolRegistry` (the manager)
- ``builtin_tools.py`` — example built-in tools
- ``enterprise.py``  — 企业工具主动 build 工厂
- ``placeholder.py`` — 企业工具占位实现（HR / 文档库 / ITSM）
- ``custom/``        — product-specific tools (auto-scanned)
"""

from .enterprise import build_enterprise_tools
from .registry import ToolRegistry

__all__ = ["ToolRegistry", "build_enterprise_tools"]
