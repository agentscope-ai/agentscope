# -*- coding: utf-8 -*-
"""企业管控中间件。

通过 ``create_app(extra_agent_middlewares=...)`` 注入，每次 agent 调用都会经过。
当前占位实现：
    - ``AuditMiddleware``: 审计留痕（谁、何时、用了哪个 agent、调了哪些工具）
    - ``DLPMiddleware``: 敏感信息脱敏（手机号、身份证、银行卡号）
"""
from .audit import AuditMiddleware
from .dlp import DLPMiddleware
from .factory import build_enterprise_middlewares

__all__ = [
    "AuditMiddleware",
    "DLPMiddleware",
    "build_enterprise_middlewares",
]
