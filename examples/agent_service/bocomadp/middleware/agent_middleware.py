# -*- coding: utf-8 -*-
"""Example agent middleware — logs each reply iteration.

Agent middlewares wrap the agent's reply loop. They are distinct
from ASGI middlewares (which wrap the HTTP app).

To add your own agent middleware:

1. Subclass ``Middleware`` from agentscope (or implement the
   middleware protocol).
2. Register it in ``main.py`` via ``MiddlewareRegistry.register()``.
3. It will be injected into every agent at build time.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from agentscope.middleware import Middleware
except ImportError:
    # Fallback base class for syntax checking
    class Middleware:  # type: ignore
        """Fallback Middleware base class."""
        async def on_reply_start(self, agent, **kwargs): ...
        async def on_reply_end(self, agent, **kwargs): ...
        async def on_tool_call_start(self, agent, **kwargs): ...
        async def on_tool_call_end(self, agent, **kwargs): ...


class LoggingMiddleware(Middleware):
    """Log each agent reply iteration for debugging."""

    async def on_reply_start(self, agent, **kwargs) -> None:
        logger.info(
            "agent reply start: agent=%s session=%s",
            getattr(agent, "name", "?"),
            getattr(agent, "state", None) and getattr(
                agent.state, "session_id", "?",
            ),
        )

    async def on_reply_end(self, agent, **kwargs) -> None:
        logger.info(
            "agent reply end: agent=%s",
            getattr(agent, "name", "?"),
        )
