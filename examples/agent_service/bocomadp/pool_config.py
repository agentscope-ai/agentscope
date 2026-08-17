# -*- coding: utf-8 -*-
"""Per-agent 沙箱池配置（并发数）存储：PG 真源 + Redis 运行时层。

写入路径（管理 API ``PUT /agents/{id}/concurrency``）：:

    PG UPSERT（持久化真源，重启不丢）
        → Redis HSET ``agentscope:pool:{agent_id}``（运行时热更新）
        → 失效 Manager 进程内缓存

读取路径（沙箱分配热路径 ``SharedPvcK8sWorkspaceManager._get_pool_size``）::

    Redis → 全局默认（不查 PG，避免每次会话都打 DB）

启动回填：``sync_all_to_redis`` 把 PG 全量刷入 Redis，
Redis 重启 / 清空后 per-agent 配置不丢。
"""

from __future__ import annotations

import time
from typing import Any

from agentscope._logging import logger

from bocomadp.config import get_app_config


# ── PG（持久化真源） ──────────────────────────────────────────────

_engine: Any = None
_lock: Any = None


async def _get_engine() -> Any:
    """懒加载独立 async engine（与框架 storage 同 URL、独立连接池）。"""
    global _engine
    if _engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        _engine = create_async_engine(
            get_app_config().db.url,
            pool_pre_ping=True,
        )
        await _ensure_table()
    return _engine


async def _ensure_table() -> None:
    """幂等建表（``agent_pool_configs``）。"""
    assert _engine is not None
    from sqlalchemy import text

    async with _engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS agent_pool_configs ("
                "agent_id VARCHAR(255) PRIMARY KEY, "
                "max_active_pods INTEGER NOT NULL, "
                "updated_at DOUBLE PRECISION NOT NULL"
                ")",
            ),
        )


async def pg_get(agent_id: str) -> int | None:
    """读 PG 中的并发配置；无记录返回 ``None``。"""
    try:
        engine = await _get_engine()
        from sqlalchemy import text

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT max_active_pods FROM agent_pool_configs "
                        "WHERE agent_id = :agent_id",
                    ),
                    {"agent_id": agent_id},
                )
            ).first()
        return int(row[0]) if row is not None else None
    except Exception as e:  # DB 不可用不影响运行时
        logger.warning("pool_config: pg_get(%s) failed: %s", agent_id, e)
        return None


async def pg_upsert(agent_id: str, size: int) -> None:
    """UPSERT 并发配置到 PG（真源）。"""
    engine = await _get_engine()
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_pool_configs "
                "(agent_id, max_active_pods, updated_at) "
                "VALUES (:agent_id, :size, :ts) "
                "ON CONFLICT (agent_id) DO UPDATE SET "
                "max_active_pods = :size, updated_at = :ts",
            ),
            {"agent_id": agent_id, "size": size, "ts": time.time()},
        )


async def pg_delete(agent_id: str) -> None:
    """删除 PG 中的并发配置（恢复默认）。"""
    engine = await _get_engine()
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM agent_pool_configs WHERE agent_id = :agent_id",
            ),
            {"agent_id": agent_id},
        )


async def pg_list_all() -> dict[str, int]:
    """PG 全量配置（启动回填用）。"""
    try:
        engine = await _get_engine()
        from sqlalchemy import text

        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT agent_id, max_active_pods "
                        "FROM agent_pool_configs",
                    ),
                )
            ).all()
        return {str(r[0]): int(r[1]) for r in rows}
    except Exception as e:
        logger.warning("pool_config: pg_list_all failed: %s", e)
        return {}


# ── Redis（运行时热更新层） ──────────────────────────────────────

_redis: Any = None


def _pool_key(agent_id: str) -> str:
    return f"agentscope:pool:{agent_id}"


async def _get_redis() -> Any:
    """懒加载长连接（连接参数来自 AppConfig）。"""
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        redis_cfg = get_app_config().redis
        _redis = aioredis.Redis(
            host=redis_cfg.host,
            port=redis_cfg.port,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis


async def redis_get(agent_id: str) -> int | None:
    """读 Redis 中的并发配置；无记录返回 ``None``。"""
    try:
        r = await _get_redis()
        val = await r.hget(_pool_key(agent_id), "max_active_pods")
        return int(val) if val is not None else None
    except Exception:
        return None


async def redis_set(agent_id: str, size: int) -> bool:
    """写 Redis；返回是否成功（失败不抛，调用方决定降级策略）。"""
    try:
        r = await _get_redis()
        await r.hset(_pool_key(agent_id), "max_active_pods", str(size))
        return True
    except Exception as e:
        logger.warning(
            "pool_config: redis_set(%s) failed: %s",
            agent_id,
            e,
        )
        return False


async def redis_delete(agent_id: str) -> None:
    """删 Redis 配置（恢复默认）。"""
    try:
        r = await _get_redis()
        await r.hdel(_pool_key(agent_id), "max_active_pods")
    except Exception as e:
        logger.warning(
            "pool_config: redis_delete(%s) failed: %s",
            agent_id,
            e,
        )


async def sync_all_to_redis() -> int:
    """启动回填：PG 全量 → Redis。返回同步条数。"""
    try:
        configs = await pg_list_all()
        r = await _get_redis()
        for agent_id, size in configs.items():
            await r.hset(_pool_key(agent_id), "max_active_pods", str(size))
        return len(configs)
    except Exception as e:
        logger.warning("pool_config: sync_all_to_redis failed: %s", e)
        return 0


__all__ = [
    "pg_get",
    "pg_upsert",
    "pg_delete",
    "pg_list_all",
    "redis_get",
    "redis_set",
    "redis_delete",
    "sync_all_to_redis",
]
