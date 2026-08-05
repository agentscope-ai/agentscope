# -*- coding: utf-8 -*-
"""企业管控中间件。

通过 ``create_app(extra_agent_middlewares=...)`` 注入，每次 agent 调用都会经过。
当前占位实现：
    - ``AuditMiddleware``: 审计留痕（谁、何时、用了哪个 agent、调了哪些工具）
"""
from .audit import AuditMiddleware
from .factory import build_enterprise_middlewares

__all__ = [
    "AuditMiddleware",
    "build_enterprise_middlewares",
]
