# -*- coding: utf-8 -*-
"""平台健康检查路由。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__
from ..config import get_app_config

platform_health_router = APIRouter(prefix="/platform/health", tags=["health"])


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    version: str
    app_name: str


@platform_health_router.get("", response_model=HealthResponse)
async def platform_health() -> HealthResponse:
    """返回服务健康状态。"""
    # 每次请求重新构建 AppConfig（config.yaml 热加载）
    config = get_app_config()
    return HealthResponse(
        status="ok",
        version=__version__,
        app_name=config.app_name,
    )
