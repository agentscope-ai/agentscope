# -*- coding: utf-8 -*-
"""企业中间件主动构建工厂（bocomadp）。

采用**主动 build** 而非 custom/ 被动扫描：
- 企业中间件（审计留痕）由 :func:`build_enterprise_middlewares` 显式构建，
  每次 agent 组装时按会话创建独立实例（user_id / session_id 直传）；
- 按 ``audit.enabled`` 配置开关决定是否装配，关闭时不产生任何中间件；
- 由 ``main.py`` 的通用中间件构建入口（``build_agent_middlewares``）调用，
  与 ``MiddlewareRegistry`` 自动扫描的内置中间件合并注入。
"""
from __future__ import annotations

from agentscope.middleware import MiddlewareBase

from ..config import get_audit_config
from .audit import AuditMiddleware
from .custom_prompt import CustomPromptMiddleware


async def build_enterprise_middlewares(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[MiddlewareBase]:
    """按配置返回当前会话需要的企业中间件列表。

    被 AgentScope 在每次 agent 组装时调用一次，因此可以在这里
    根据用户/会话返回不同的中间件组合。

    CustomPromptMiddleware 无 per-session 状态（提示词从请求级
    ContextVar 读取），每次组装构建新实例即可，无额外成本。
    """
    middlewares: list[MiddlewareBase] = [
        CustomPromptMiddleware(),
    ]

    if get_audit_config().enabled:
        middlewares.append(
            AuditMiddleware(user_id=user_id, session_id=session_id),
        )

    return middlewares
