# -*- coding: utf-8 -*-
"""Example agent middleware — logs each reply iteration.

Agent middlewares wrap the agent's reply loop. They are distinct
from ASGI middlewares (which wrap the HTTP app).

To add your own agent middleware:

1. Subclass ``MiddlewareBase`` from agentscope (the only base class
   the framework exports — ``agentscope.middleware.Middleware`` does
   NOT exist, importing it always falls back to the stub below).
2. Register it in ``main.py`` via ``MiddlewareRegistry.register()``.
3. It will be injected into every agent at build time.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

try:
    from agentscope.middleware import MiddlewareBase
except ImportError:
    # Fallback base class for offline syntax checking. 实现了 Agent
    # 构造时依赖的最小接口（is_implemented / list_tools /
    # get_middleware_key），避免 AttributeError。
    class MiddlewareBase:  # type: ignore
        """Fallback MiddlewareBase for syntax checking."""

        _is_agent_middleware = True

        def is_implemented(self, hook_name: str) -> bool:
            """检查钩子是否被覆写（与框架实现一致）。"""
            base = getattr(MiddlewareBase, hook_name, None)
            sub = getattr(type(self), hook_name, None)
            return base is not sub

        async def list_tools(self) -> list:
            """中间件提供的工具列表。"""
            return []

        async def get_middleware_key(self) -> str:
            """中间件状态键。"""
            return self.__class__.__name__

# 统一打标记：无论 import 成功（agentscope.middleware.MiddlewareBase）
# 还是 fallback，都让 MiddlewareRegistry._scan_module_for_middlewares
# 能通过 _is_agent_middleware 属性识别实例。
MiddlewareBase._is_agent_middleware = True  # type: ignore[attr-defined]


class LoggingMiddleware(MiddlewareBase):
    """记录每次 reply 迭代，用于调试。

    使用 ``MiddlewareBase`` 标准洋葱钩子 ``on_reply``（框架 Agent
    构造时通过 ``is_implemented("on_reply")`` 识别并注入）。
    """

    async def on_reply(
        self,
        agent: "Any",  # Agent
        input_kwargs: dict,
        next_handler: AsyncGenerator,
    ) -> AsyncGenerator:
        logger.info(
            "agent reply start: agent=%s session=%s",
            getattr(agent, "name", "?"),
            getattr(getattr(agent, "state", None), "session_id", "?"),
        )
        async for event in next_handler():
            yield event
        logger.info(
            "agent reply end: agent=%s",
            getattr(agent, "name", "?"),
        )


# 模块级实例 —— MiddlewareRegistry.load_builtin() 会扫描并自动注册。
# 新增内置 middleware：实例化后在此导出即可。
logging_mw = LoggingMiddleware()
