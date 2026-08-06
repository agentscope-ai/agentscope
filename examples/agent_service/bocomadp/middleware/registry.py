# -*- coding: utf-8 -*-
"""Agent middleware registry.

Manages agent-level middlewares that wrap the agent's reply loop
(distinct from ASGI middlewares that wrap the HTTP app).

Agent middlewares are injected into :class:`AgentBuilder` which passes
them to the ``Agent`` constructor's ``middlewares`` parameter.

Usage::

    from bocomadp.middleware import MiddlewareRegistry

    registry = MiddlewareRegistry()
    registry.load_builtin()  # loads from agent_middleware.py
    registry.register(my_custom_middleware)

    # In AgentBuilder.build():
    middlewares = registry.list_middlewares()
    agent = Agent(..., middlewares=middlewares)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MiddlewareRegistry:
    """Registry of agent-level middlewares."""

    def __init__(self) -> None:
        self._middlewares: list[Any] = []
        self._names: set[str] = set()

    def register(self, middleware: Any) -> None:
        """Register an agent middleware instance. Idempotent — duplicates by class name are skipped."""
        name = type(middleware).__name__
        if name in self._names:
            logger.debug("agent middleware already registered: %s", name)
            return
        self._middlewares.append(middleware)
        self._names.add(name)
        logger.info("agent middleware registered: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a middleware by class name."""
        self._middlewares = [
            m for m in self._middlewares
            if type(m).__name__ != name
        ]
        self._names.discard(name)

    def list_middlewares(self) -> list[Any]:
        """Return all registered middlewares."""
        return list(self._middlewares)

    def list_middleware_names(self) -> list[str]:
        """Return all registered middleware class names."""
        return sorted(self._names)

    def load_builtin(self) -> None:
        """加载 agent_middleware.py 中所有模块级 Middleware 实例。

        在该模块顶部实例化并导出（如 ``logging_mw = LoggingMiddleware()``），
        重启后自动注册。
        """
        try:
            from . import agent_middleware  # noqa: F401
            self._scan_module_for_middlewares(agent_middleware)
        except ImportError:
            logger.warning("agent_middleware module not found")
        except Exception:
            logger.exception("failed to load built-in agent middlewares")

    def load_custom(self) -> None:
        """自动扫描 ``custom/`` 包下所有子模块的 Middleware 实例。

        在 ``custom/`` 下新建任意 ``.py`` 文件，实例化 Middleware 子类并
        在模块级导出（如 ``audit_mw = AuditMiddleware()``），重启后自动
        注册，无需修改 main.py。
        """
        try:
            import importlib
            import pkgutil
            from . import custom as _custom_pkg
        except ImportError:
            logger.debug("custom middlewares package not found; skipping")
            return
        for _finder, modname, _ispkg in pkgutil.walk_packages(
            _custom_pkg.__path__,
            prefix=_custom_pkg.__name__ + ".",
        ):
            try:
                mod = importlib.import_module(modname)
            except Exception:
                logger.warning(
                    "failed to import custom middleware module: %s",
                    modname,
                    exc_info=True,
                )
                continue
            self._scan_module_for_middlewares(mod)

    def _scan_module_for_middlewares(self, mod: Any) -> None:
        """扫描模块命名空间，注册所有 ``_is_agent_middleware`` 标记的实例。

        判定条件：
        - 有 ``_is_agent_middleware`` 属性且为 True（Middleware 基类设置）
        - 是实例而非类（``not isinstance(obj, type)``），因为 middleware
          需要带状态/参数实例化后才可注册
        """
        for name in dir(mod):
            obj = getattr(mod, name)
            if (
                getattr(obj, "_is_agent_middleware", False)
                and not isinstance(obj, type)
            ):
                self.register(obj)


__all__ = ["MiddlewareRegistry"]
