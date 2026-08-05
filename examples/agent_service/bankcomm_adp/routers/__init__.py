# -*- coding: utf-8 -*-
"""平台自有路由（与 AgentScope 内置路由并列）。"""
from .health import router as health_router
from .skill_router import router as skill_router

__all__ = ["health_router", "skill_router"]
