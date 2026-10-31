#-- coding: utf-8 --
"""Redis Cluster message bus implementation.

This module provides:
1. RedisClusterMessageBus - a RedisMessageBus compatible with Redis Cluster
    (replaces Pub/Sub with Streams since Redis Cluster doesn't support Pub/Sub)
2. Monkey-patches for RedisStorage and RedisMessageBus to accept RedisCluster
    connection pools directly
"""
import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Callable 

from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from redis.asyncio.cluster import RedisCluster
from redis.asyncio import Redis


#------------------------------------------------------------------
#RedisClusterMessageBus - Stream-based channels for Cluster mode
#------------------------------------------------------------------

#Key prefix for the transient broadcast (Mode D)
#In Redis Cluster we replace Pub/Sub with a Stream-based channel
_RCB_STREAM_PREFIX = "agentscope:rcb:stream:"
_RCB_POLL_INTERVAL = 1.0 # seconds to poll each Stream read


class RedisClusterMessageBus(RedisMessageBus):
    """RedisMessageBus compatible with Redis Cluster.
    
    Redis Cluster does NOT support Pub/Sub. This class overrides the
    ``publish`` / ``subscribe pair`` (Mode D -- transient broadcast)
    with a Stream-based equivalent so that the wakeup dispatcher can
    operate in a cluster environment.
    """
    
    async def publish(self, key: str, payload: dict) -> None:
        """Publish using XADD (replaces Pub/Sub in Cluster mode)."""
        stream_key = f"{_RCB_STREAM_PREFIX}{key}"
        await self._client.xadd(stream_key, {"payload": json.dumps(payload)})
    
    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Poll a Stream instead of subscribing to Pub/Sub."""
        stream_key = f"{_RCB_STREAM_PREFIX}{key}"
        last_id = "0"
        
        if on_ready is not None:
            on_ready()
            
        while True:
            try:
                response = await self._client.xread(
                    {stream_key: last_id},
                    count=100,
                    block=int(_RCB_POLL_INTERVAL * 1000),
                )
            except Exception: # noqa: BLE001 -- resilience; keep polling                
                await asyncio.sleep(_RCB_POLL_INTERVAL)
                continue
            
            if not response:
                continue
            
            for _stream_name, entries in response:
                for entry_id, fields in entries:
                    last_id = entry_id
                    raw = fields.get("payload")
                    if raw is None:
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    
    # ------------------------------------------------------------------    
    # # Distributed-lock and registry are unaffected (all supported in    
    # # Redis Cluster). No overrides needed.    
    # # ------------------------------------------------------------------


#------------------------------------------------------------------
#Monkey-patch __aenter__ to accept RedisCluster / Redis directly
#------------------------------------------------------------------\

async def _patch_redis_storage_aenter(self: RedisStorage) -> RedisStorage:
    """Patch for RedisStorage to accept RedisCluster connection pools."""
    try:
        import redis.asyncio as aioredis
    except ImportError as e:
        raise ImportError(
            "The 'redis' package is required for RedisStorage. "
            "Install it with: pip install redis[async]",
        ) from e
        
    if self._external_pool is not None:
        pool = self._external_pool
    else:
        self._owned_pool = aioredis.ConnectionPool(
            host=self._host,
            port=self._port,
            db=self._db,
            password=self._password,
            decode_responses=True,
            **self._kwargs,
        )
        pool = self._owned_pool
        
    if isinstance(pool, (RedisCluster, Redis)):
        self._client = pool
    else:
        self._client = aioredis.Redis(connection_pool=pool)
    return self

async def _patch_redis_messagebus_aenter(
    self: RedisMessageBus,
) -> RedisMessageBus:
    """Patch for RedisMessageBus to accept RedisCluster connection pools."""
    try:
        import redis.asyncio as aioredis
    except ImportError as e:
        raise ImportError(
            "The 'redis' package is required for RedisMessageBus. "
            "Install it with: pip install redis[async]",
        ) from e
        
    if self._external_pool is not None:
        pool = self._external_pool
    else:
        self._owned_pool = aioredis.ConnectionPool(
            host=self._host,
            port=self._port,
            db=self._db,
            password=self._password,
            decode_responses=True,
            **self._kwargs,
            )
        pool = self._owned_pool
        
    if isinstance(pool, (RedisCluster, Redis)):
        self._client = pool
    else:
        self._client = aioredis.Redis(connection_pool=pool)
        return self
    
    
#Apply monkey-patches at module import time
RedisStorage.__aenter__ = _patch_redis_storage_aenter
RedisMessageBus.__aenter__ = _patch_redis_messagebus_aenter

