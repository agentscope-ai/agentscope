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

# 统一打标记：无论 import 成功（agentscope.middleware.Middleware）
# 还是 fallback，都让 MiddlewareRegistry._scan_module_for_middlewares
# 能通过 _is_agent_middleware 属性识别实例。
Middleware._is_agent_middleware = True  # type: ignore[attr-defined]


class LoggingMiddleware(Middleware):
    """记录每次 reply 迭代，用于调试。"""

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


# 模块级实例 —— MiddlewareRegistry.load_builtin() 会扫描并自动注册。
# 新增内置 middleware：实例化后在此导出即可。
logging_mw = LoggingMiddleware()
