# -*- coding: utf-8 -*-
"""中间件工厂。

``create_app`` 的 ``extra_agent_middlewares`` 参数要求一个
``async (user_id, agent_id, session_id) -> list[MiddlewareBase]`` 工厂。
本模块封装该工厂，按配置装配企业管控中间件链。
"""
from __future__ import annotations

from agentscope.middleware import MiddlewareBase

from ..config.audit_config import get_audit_config
from .audit import AuditMiddleware


async def build_enterprise_middlewares(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[MiddlewareBase]:
    """按配置返回当前会话需要的企业中间件列表。

    被 AgentScope 在每次 agent 组装时调用一次，因此可以在这里
    根据用户/会话返回不同的中间件组合。
    """
    middlewares: list[MiddlewareBase] = []

    if get_audit_config().enabled:
        middlewares.append(
            AuditMiddleware(user_id=user_id, session_id=session_id),
        )

    return middlewares
