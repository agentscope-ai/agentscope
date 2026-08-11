# -*- coding: utf-8 -*-
"""deerflow 路由的 FastAPI 依赖注入（单例取自 app.state）。"""

from __future__ import annotations

from fastapi import Header, Request

from bocomadp.config import load_agents_from_yaml

from .bridge import BusBridge
from .runs import RunManager

# 缺省用户：SDK 请求不携带 X-User-ID 时使用（本地/单租户部署；生产接入
# 认证后可收紧为必填）。
DEERFLOW_DEFAULT_USER_ID = "default"

_DEFAULT_AGENT_ID: str | None = None


def _default_agent_id() -> str:
    """config.yaml 首个 seed agent；读取失败时兜底 customer_service。"""
    global _DEFAULT_AGENT_ID
    if _DEFAULT_AGENT_ID is None:
        try:
            for entry in load_agents_from_yaml():
                _DEFAULT_AGENT_ID = entry.agent_id
                break
        except Exception:  # noqa: BLE001 —— 默认值仅为兜底，失败不阻断
            pass
        if _DEFAULT_AGENT_ID is None:
            _DEFAULT_AGENT_ID = "customer_service"
    return _DEFAULT_AGENT_ID


async def get_deerflow_user_id(
    x_user_id: str | None = Header(
        default=None,
        description="Caller's user ID. Optional; defaults to "
        f"{DEERFLOW_DEFAULT_USER_ID!r} to stay compatible with the "
        "LangGraph SDK (which does not send this header).",
    ),
) -> str:
    """Return the caller's user ID, falling back to the default user.

    Unlike the native ``get_current_user_id`` (header required), this
    dependency tolerates missing headers so the deer-flow frontend /
    LangGraph SDK can call the endpoints without modification.
    """
    return x_user_id.strip() if x_user_id and x_user_id.strip() else DEERFLOW_DEFAULT_USER_ID


async def get_run_manager(request: Request) -> RunManager:
    """Return the application-wide :class:`RunManager`."""
    return request.app.state.run_manager


async def get_bridge(request: Request) -> BusBridge:
    """Return the application-wide :class:`BusBridge`."""
    return request.app.state.bus_bridge


__all__ = [
    "get_run_manager",
    "get_bridge",
    "get_deerflow_user_id",
    "_default_agent_id",
]
