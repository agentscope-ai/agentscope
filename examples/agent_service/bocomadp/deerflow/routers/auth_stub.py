# -*- coding: utf-8 -*-
"""deer-flow 前端认证桩：bocomadp 无独立用户体系，返回固定管理员用户。

deer-flow 前端的 SSR 鉴权（/api/deerflow/v1/auth/me）与初始化探测
（/api/deerflow/v1/auth/setup-status）在后端替换为 bocomadp 后不再有真实实现，
此处提供与前端 ``AUTH_DISABLED_USER``（frontend/src/core/auth/auth-disabled-user.ts）
同构的固定用户，保证前端零改动可用。单租户/本地联调适用；生产接入
真实认证后应删除本模块。
"""

from __future__ import annotations

from fastapi import APIRouter

auth_stub_router = APIRouter(
    prefix="/deerflow/v1/auth", tags=["deerflow-auth-stub"]
)

# 注意：本路由挂载在 main.py 的 /api 子应用下，对外路径为
# /api/deerflow/v1/auth/...；deer-flow 前端旧路径 /api/v1/auth/... 由
# nginx 网关 rewrite 兼容。

# 与前端 auth-disabled-user.ts 的 AUTH_DISABLED_USER 保持一致：
# id=default、email 合法、system_role=admin，needs_setup=False。
FIXED_ADMIN_USER: dict = {
    "id": "default",
    "email": "default@test.local",
    "system_role": "admin",
    "needs_setup": False,
    "oauth_provider": None,
}


@auth_stub_router.get("/me")
async def auth_me() -> dict:
    """返回固定管理员用户，跳过登录/会话校验。"""
    return FIXED_ADMIN_USER


@auth_stub_router.get("/setup-status")
async def auth_setup_status() -> dict:
    """系统初始化状态：已初始化，避免前端跳转 setup 引导页。"""
    return {"needs_setup": False}
