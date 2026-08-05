# -*- coding: utf-8 -*-
"""健康检查路由。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__
from ..config.settings_config import get_settings

router = APIRouter(prefix="/platform/health", tags=["health"])


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    version: str
    app_name: str


@router.get("", response_model=HealthResponse)
async def health() -> HealthResponse:
    """返回服务健康状态。"""
    return HealthResponse(
        status="ok",
        version=__version__,
        app_name=get_settings().app_name,
    )
