# -*- coding: utf-8 -*-
"""认证上下文模块（对齐 deer-flow ``community/common/auth_context.py``）。

通过 ContextVar 在 deerflow 路由层与工具后端之间传递解析后的认证信息，
避免在每个 tool call 中重复解析 custom_params。解析入口为
:func:`resolve_auth_params`（对齐 deer-flow ``_resolve_auth_params``），
优先级：guwp-token > jrt-auth-code > okic-token > muwp-user > none。

ContextVar 随 ``asyncio.create_task`` 复制到后台 run 任务，工具中间件 /
后端在 run 任务内经 :func:`get_resolved_auth` 读取；路由层 spawn 后
reset 不影响已创建的子任务（同 ``custom_params`` 模块的传播语义）。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass
class ResolvedAuth:
    """解析后的认证信息。

    ``auth_mode`` 表示最终选用的认证方式，优先级：
    guwp-token > jrt-auth-code > okic-token > muwp-user > none。
    """

    auth_mode: Literal[
        "guwp-token",
        "jrt-auth-code",
        "okic-token",
        "muwp-user",
        "none",
    ]
    guwp_token: str = ""
    jrt_auth_code: str = ""
    okic_token: str = ""
    okic_type: str = ""
    muwp_user: dict[str, Any] = field(default_factory=dict)


_auth_ctx: ContextVar[ResolvedAuth] = ContextVar(
    "resolved_auth",
    default=ResolvedAuth(auth_mode="none"),
)


def set_resolved_auth(auth: ResolvedAuth) -> Token:
    """设置当前上下文的认证信息，返回 reset 用的 token。"""
    return _auth_ctx.set(auth)


def reset_resolved_auth(token: Token) -> None:
    """恢复 ContextVar 到 :func:`set_resolved_auth` 之前的值。"""
    _auth_ctx.reset(token)


def get_resolved_auth() -> ResolvedAuth:
    """获取当前上下文的认证信息（工具后端中调用）。"""
    return _auth_ctx.get()


def resolve_auth_params(
    custom_params: dict[str, Any] | None,
) -> ResolvedAuth:
    """从 custom_params 解析认证方案（对齐 deer-flow ``_resolve_auth_params``）。

    优先级：guwp-token > jrt-auth-code > okic-token > muwp-user > none。
    任一方案的凭据为空串 / 缺失则跳过，全部缺失时返回 ``none``。
    """
    if not custom_params:
        return ResolvedAuth(auth_mode="none")

    guwp_token = str(custom_params.get("guwp_token") or "")
    jrt_auth_code = str(custom_params.get("jrt_auth_code") or "")
    okic_token = str(custom_params.get("okic_token") or "")
    okic_type = str(custom_params.get("okic_type") or "")
    muwp_user = custom_params.get("muwp_user") or {}
    if not isinstance(muwp_user, dict):
        muwp_user = {}

    if guwp_token:
        return ResolvedAuth(auth_mode="guwp-token", guwp_token=guwp_token)
    if jrt_auth_code:
        return ResolvedAuth(
            auth_mode="jrt-auth-code",
            jrt_auth_code=jrt_auth_code,
        )
    if okic_token:
        return ResolvedAuth(
            auth_mode="okic-token",
            okic_token=okic_token,
            okic_type=okic_type,
        )
    if muwp_user:
        return ResolvedAuth(auth_mode="muwp-user", muwp_user=muwp_user)
    return ResolvedAuth(auth_mode="none")


__all__ = [
    "ResolvedAuth",
    "set_resolved_auth",
    "reset_resolved_auth",
    "get_resolved_auth",
    "resolve_auth_params",
]
