# -*- coding: utf-8 -*-
"""平台自有路由（与 AgentScope 内置路由并列）。"""
from .health import router as health_router

__all__ = ["health_router"]
