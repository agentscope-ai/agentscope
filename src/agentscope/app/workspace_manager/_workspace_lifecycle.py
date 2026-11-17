# -*- coding: utf-8 -*-
"""Atomic usage leases and idle collection for shared sandboxes."""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class WorkspaceLifecycle(ABC):
    """Coordinate users and collectors in one sandbox namespace.

    Implementations must atomically exclude acquisition from a claimed
    pause. A pause claim must not expire: the sandbox API cannot fence a
    delayed pause RPC. An interrupted collector therefore fails closed.
    """

    @abstractmethod
    async def update(
        self,
        workspace_id: str,
        operation: str,
        token: str,
        duration: float,
    ) -> int:
        """Perform an atomic lifecycle operation.

        Args:
            workspace_id (`str`): Stable workspace identity.
            operation (`str`): acquire, renew, release, claim or finish.
            token (`str`): Unique borrower or collector identity.
            duration (`float`): Lease duration or idle threshold, seconds.

        Returns:
            `int`: Generation on acquisition, one on success, zero on
            refusal. Generations start at one.
        """

    @abstractmethod
    async def candidates(self) -> list[str]:
        """List tracked workspaces, including orphaned usage leases.

        Returns:
            `list[str]`: Workspace identities to check for collection.
        """


@dataclass
class _Entry:
    leases: dict[str, float] = field(default_factory=dict)
    idle: float = 0
    collector: str | None = None
    generation: int = 1
    tracked: bool = False


class LocalWorkspaceLifecycle(WorkspaceLifecycle):
    """In-process lifecycle coordinator; share one instance per namespace.

    For multiple service processes use :class:`RedisWorkspaceLifecycle`.
    """

    def __init__(self) -> None:
        """Create an empty lifecycle registry."""
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def candidates(self) -> list[str]:
        """Return tracked workspace identities."""
        async with self._lock:
            return [wid for wid, e in self._entries.items() if e.tracked]

    async def update(
        self,
        workspace_id: str,
        operation: str,
        token: str,
        duration: float,
    ) -> int:
        """Atomically update a lease or collector using monotonic time."""
        async with self._lock:
            now = time.monotonic()
            entry = self._entries.setdefault(workspace_id, _Entry(idle=now))
            expired = [t for t, end in entry.leases.items() if end <= now]
            if expired:
                last = max(entry.leases[t] for t in expired)
                for stale in expired:
                    del entry.leases[stale]
                if not entry.leases:
                    entry.idle = max(entry.idle, last)
            if operation == "acquire":
                if entry.collector:
                    return 0
                entry.leases[token] = now + duration
                entry.tracked = True
                return entry.generation
            if operation == "renew":
                if token not in entry.leases or entry.collector:
                    return 0
                entry.leases[token] = now + duration
                return 1
            if operation == "release":
                if entry.leases.pop(token, None) is not None:
                    if not entry.leases:
                        entry.idle = now
                return 1
            if operation == "claim":
                if entry.collector or entry.leases:
                    return 0
                if now - entry.idle < duration:
                    return 0
                entry.collector = token
                return 1
            if operation == "finish":
                if entry.collector != token:
                    return 0
                entry.collector = None
                entry.generation += 1
                entry.tracked = False
                entry.idle = now
                return 1
            raise ValueError(f"Unknown lifecycle operation: {operation}")


# All keys share a hash tag, so the operation is Redis Cluster compatible.
# Redis TIME avoids disagreements between application-node clocks.
_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local op, token, duration = ARGV[1], ARGV[2], tonumber(ARGV[3])
local wid = ARGV[4]
local stale = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now,
                         'WITHSCORES')
local last = 0
for i = 2, #stale, 2 do last = math.max(last, tonumber(stale[i])) end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if last > 0 and redis.call('ZCARD', KEYS[2]) == 0 then
    redis.call('HSET', KEYS[1], 'idle', last)
end
redis.call('HSETNX', KEYS[1], 'generation', 1)
redis.call('HSETNX', KEYS[1], 'idle', now)
local collector = redis.call('HGET', KEYS[1], 'collector')
if op == 'acquire' then
    if collector then return 0 end
    redis.call('SADD', KEYS[3], wid)
    redis.call('ZADD', KEYS[2], now + duration, token)
    return tonumber(redis.call('HGET', KEYS[1], 'generation'))
elseif op == 'renew' then
    if collector or not redis.call('ZSCORE', KEYS[2], token) then
        return 0
    end
    redis.call('ZADD', KEYS[2], now + duration, token)
    return 1
elseif op == 'release' then
    local removed = redis.call('ZREM', KEYS[2], token)
    if removed == 1 and redis.call('ZCARD', KEYS[2]) == 0 then
        redis.call('HSET', KEYS[1], 'idle', now)
    end
    return 1
elseif op == 'claim' then
    if collector or redis.call('ZCARD', KEYS[2]) > 0 then return 0 end
    local idle = tonumber(redis.call('HGET', KEYS[1], 'idle'))
    if now - idle < duration then return 0 end
    redis.call('HSET', KEYS[1], 'collector', token)
    return 1
elseif op == 'finish' then
    if collector ~= token then return 0 end
    redis.call('HDEL', KEYS[1], 'collector')
    redis.call('HINCRBY', KEYS[1], 'generation', 1)
    redis.call('SREM', KEYS[3], wid)
    redis.call('HSET', KEYS[1], 'idle', now)
    return 1
end
return redis.error_reply('Unknown lifecycle operation')
"""


class RedisWorkspaceLifecycle(WorkspaceLifecycle):
    """Redis-backed shared lifecycle registry with renewable usage leases.

    Use the same Redis database and namespace on every service node that
    shares sandboxes. Redis must retain these keys (no eviction policy).
    Collector claims deliberately have no TTL. Following a collector
    crash or an ambiguous pause failure, stop that collector and confirm
    the remote operation has completed before removing its ``collector``
    hash field. Automatically expiring this field would admit new users
    while a stale collector could still pause their sandbox.
    """

    def __init__(self, client: Any, namespace: str = "opensandbox") -> None:
        """Bind an asynchronous Redis client owned by the caller.

        Args:
            client (`Any`): ``redis.asyncio.Redis`` compatible client.
            namespace (`str`): Shared sandbox deployment identifier.

        Raises:
            `ValueError`: Namespace contains Redis hash-tag delimiters.
        """
        if "{" in namespace or "}" in namespace:
            raise ValueError("Namespace cannot contain braces")
        self._client = client
        self._prefix = f"agentscope:workspace:{{{namespace}}}"

    async def candidates(self) -> list[str]:
        """Return tracked workspace identities from the shared registry."""
        members = await self._client.smembers(self._prefix + ":index")
        return [m.decode() if isinstance(m, bytes) else m for m in members]

    async def update(
        self,
        workspace_id: str,
        operation: str,
        token: str,
        duration: float,
    ) -> int:
        """Execute one lifecycle transition atomically using Redis time."""
        return int(
            await self._client.eval(
                _SCRIPT,
                3,
                f"{self._prefix}:{workspace_id}:state",
                f"{self._prefix}:{workspace_id}:leases",
                self._prefix + ":index",
                operation,
                token,
                duration,
                workspace_id,
            ),
        )
