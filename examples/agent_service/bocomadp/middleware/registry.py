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

    def register(self, middleware: Any) -> None:
        """Register an agent middleware instance."""
        self._middlewares.append(middleware)
        name = type(middleware).__name__
        logger.info("agent middleware registered: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a middleware by class name."""
        self._middlewares = [
            m for m in self._middlewares
            if type(m).__name__ != name
        ]

    def list_middlewares(self) -> list[Any]:
        """Return all registered middlewares."""
        return list(self._middlewares)

    def load_builtin(self) -> None:
        """Load built-in agent middlewares from :mod:`agent_middleware`."""
        try:
            from . import agent_middleware  # noqa: F401
            # Built-in middlewares are created and registered in
            # the module-level code of agent_middleware.py
        except ImportError:
            logger.warning("agent_middleware module not found")
        except Exception:
            logger.exception("failed to load built-in agent middlewares")


__all__ = ["MiddlewareRegistry"]
