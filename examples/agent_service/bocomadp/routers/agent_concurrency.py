# -*- coding: utf-8 -*-
"""Per-agent 沙箱并发（池大小）管理 API。

GET    /agents/{agent_id}/concurrency   — 查询生效并发
PUT    /agents/{agent_id}/concurrency   — 设置并发（写 PG + 同步 Redis）
DELETE /agents/{agent_id}/concurrency   — 恢复默认（删 PG + 删 Redis）

真源为 PG（``agent_pool_configs`` 表，重启不丢），Redis 是运行时
热更新层（``SharedPvcK8sWorkspaceManager._get_pool_size`` 直接读）。
PUT 双写 PG + Redis，并在写后失效 Manager 进程内缓存（立即生效，
无需等 5s TTL 过期）；Redis 同步失败不报错——PG 已持久化，且
``sync_all_to_redis`` 在服务启动时会回填。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agentscope.app.deps import get_current_user_id

from bocomadp.pool_config import (
    pg_delete,
    pg_get,
    pg_upsert,
    redis_delete,
    redis_get,
    redis_set,
)
from bocomadp.workspace.config import K8sWorkspaceConfig

logger = logging.getLogger("bocomadp.agent_concurrency")

agent_concurrency_router = APIRouter(
    prefix="/agents",
    tags=["agent-concurrency"],
)


def _global_default() -> int:
    """全局默认池大小（与 SharedPvcK8sWorkspaceManager 构造默认同源）。"""
    return K8sWorkspaceConfig().max_active_pods


class ConcurrencyRequest(BaseModel):
    """设置智能体最大并发沙箱数。"""

    max_active_pods: int = Field(
        ge=0,
        description="最大并发沙箱 Pod 数；0 = 不池化（按需创建）",
    )


async def _resolve_agent(
    request: Request,
    user_id: str,
    agent_id: str,
) -> Any:
    """按调用者 scope 解析 agent（与 agent_tools 一致）。"""
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        return None
    try:
        return await storage.get_agent(user_id, agent_id)
    except Exception:  # noqa: BLE001
        return None


def _invalidate_manager_cache(request: Request, agent_id: str) -> None:
    """失效 SharedPvcK8sWorkspaceManager 进程内缓存（若存在）。

    剥掉 WhitelistWorkspaceManager 包装；本地模式（K8s 未启用）
    没有该 Manager，直接跳过。
    """
    try:
        manager = getattr(
            request.app.state.runtime,
            "workspace_manager",
            None,
        )
        inner = getattr(manager, "_inner", None)
        invalidate = getattr(inner, "invalidate_pool_size", None)
        if callable(invalidate):
            invalidate(agent_id)
    except Exception:  # noqa: BLE001
        pass


@agent_concurrency_router.get(
    "/{agent_id}/concurrency",
    summary="查询智能体沙箱并发配置",
)
async def get_agent_concurrency(
    agent_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """生效值解析链：Redis（运行时）→ PG（持久化真源）→ 全局默认。"""
    agent = await _resolve_agent(request, user_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    value = await redis_get(agent_id)
    source = "redis"
    if value is None:
        value = await pg_get(agent_id)
        source = "pg" if value is not None else "default"
        if value is None:
            value = _global_default()
    return {
        "agent_id": agent_id,
        "max_active_pods": value,
        "source": source,
    }


@agent_concurrency_router.put(
    "/{agent_id}/concurrency",
    summary="设置智能体沙箱并发（写 PG + 同步 Redis）",
)
async def set_agent_concurrency(
    agent_id: str,
    body: ConcurrencyRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    agent = await _resolve_agent(request, user_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 1. PG 真源（持久化，重启不丢）
    await pg_upsert(agent_id, body.max_active_pods)

    # 2. Redis 运行时层（同步更新，热路径直接读它）
    redis_synced = await redis_set(agent_id, body.max_active_pods)

    # 3. 失效 Manager 进程内缓存（新配置立即生效）
    _invalidate_manager_cache(request, agent_id)

    logger.info(
        "agent_concurrency: %s -> max_active_pods=%d (redis_synced=%s)",
        agent_id,
        body.max_active_pods,
        redis_synced,
    )
    return {
        "agent_id": agent_id,
        "max_active_pods": body.max_active_pods,
        "redis_synced": redis_synced,
    }


@agent_concurrency_router.delete(
    "/{agent_id}/concurrency",
    summary="恢复默认并发（删 PG + 删 Redis）",
)
async def reset_agent_concurrency(
    agent_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    agent = await _resolve_agent(request, user_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    await pg_delete(agent_id)
    await redis_delete(agent_id)
    _invalidate_manager_cache(request, agent_id)

    logger.info("agent_concurrency: %s -> reset to default", agent_id)
    return {
        "agent_id": agent_id,
        "max_active_pods": _global_default(),
    }


__all__ = ["agent_concurrency_router"]
