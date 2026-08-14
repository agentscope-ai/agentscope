# -*- coding: utf-8 -*-
"""Shared pytest fixtures for bocomadp tests (no real Redis needed)."""
from __future__ import annotations

import re

import pytest


class _FakePipeline:
    """Accumulate pipelined commands, run them sequentially on execute()."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._commands: list[tuple[str, tuple]] = []

    def eval(self, *args):
        self._commands.append(("eval", args))
        return self

    def exists(self, *args):
        self._commands.append(("exists", args))
        return self

    async def execute(self) -> list:
        results = []
        for method, args in self._commands:
            fn = getattr(self._redis, method)
            results.append(await fn(*args))
        return results


class FakeRedis:
    """In-memory stand-in for the redis.asyncio client.

    Supports only the commands ConcurrencyGuard uses:
    incr / decr / hset / hdel / hgetall / exists / get / set / eval / pipeline.
    """

    def __init__(self) -> None:
        self._strings: dict[str, int] = {}
        self._hashes: dict[str, dict[str, str]] = {}

    async def incr(self, key: str) -> int:
        self._strings[key] = self._strings.get(key, 0) + 1
        return self._strings[key]

    async def decr(self, key: str) -> int:
        self._strings[key] = self._strings.get(key, 0) - 1
        return self._strings[key]

    async def set(self, key: str, value: int) -> None:
        self._strings[key] = value

    async def hset(self, key: str, field: str, value: str) -> int:
        h = self._hashes.setdefault(key, {})
        h[field] = value
        return 1

    async def hdel(self, key: str, field: str) -> int:
        h = self._hashes.get(key)
        if h is None or field not in h:
            return 0
        del h[field]
        if not h:
            # 模拟真实 Redis 语义:hash 最后一个 field 被删后 key 一并移除,
            # 否则空 hash 残留会让 exists() 误判 key 仍存在
            del self._hashes[key]
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    async def hget(self, key: str, field: str) -> str | None:
        h = self._hashes.get(key)
        if h is None:
            return None
        return h.get(field)

    async def get(self, key: str) -> str | None:
        """当前字符串值(测试断言用)。"""
        val = self._strings.get(key)
        return str(val) if val is not None else None

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self._strings or k in self._hashes)

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        """模拟 ConcurrencyGuard 使用的 Lua 脚本。

        - ``_REGISTER_LUA``(含 HSET):条件注册——旧条目存在且锁 key 消失
          则先释放旧名额再覆盖写入,返回 1;否则直接覆盖,返回 0。
        - ``_RECONCILE_LUA``(含 HGETALL,不含 HSET):整体对账——遍历注册表,
          判锁 → 判定候选(seen 立即 / grace 兜底)→ HDEL + DECR global +
          DECR user 原子,返回清理条数。
        无法识别的脚本抛 NotImplementedError。
        """
        if "HSET" in script:
            # _REGISTER_LUA:KEYS[1]=注册表, ARGV[1]=sid, ARGV[2]=新value,
            # ARGV[3]=锁 key 前缀
            key, sid, new_value, lock_prefix = args[0], args[1], args[2], args[3]
            h = self._hashes.get(key)
            old = h.get(sid) if h is not None else None
            if old is not None:
                lock_key = lock_prefix + sid
                if lock_key not in self._strings and lock_key not in self._hashes:
                    # 旧条目存在且旧对话已结束(锁消失)→ 释放旧名额
                    del h[sid]
                    self._strings["agentscope:running:global"] = (
                        self._strings.get("agentscope:running:global", 0) - 1
                    )
                    m = re.match(r"^(.+):\d+:\w+$", old) or re.match(
                        r"^(.+):\d+$",
                        old,
                    )
                    if not m:
                        # 与真实 Lua 一致:先剥掉 :seen 后缀再解析 uid
                        m = re.match(r"^(.+):\d+:\w+$", old.removesuffix(":seen")) or re.match(
                            r"^(.+):\d+$",
                            old.removesuffix(":seen"),
                        )
                    if m:
                        user_key = f"agentscope:running:user:{m.group(1)}"
                        self._strings[user_key] = self._strings.get(user_key, 0) - 1
                    self._hashes.setdefault(key, {})[sid] = new_value
                    return 1
            self._hashes.setdefault(key, {})[sid] = new_value
            return 0
        if "HGETALL" not in script:
            raise NotImplementedError(f"unrecognized Lua script: {script!r}")
        # _RECONCILE_LUA:KEYS[1]=注册表, ARGV[1]=锁 key 前缀, ARGV[2]=grace_secs,
        # ARGV[3]=now。整体对账语义与真实 Lua 一致,返回清理条数。
        key, lock_prefix, grace, now = args[0], args[1], args[2], args[3]
        grace = float(grace)
        now = float(now)
        h = self._hashes.get(key) or {}
        cleaned = 0
        for sid, value in list(h.items()):
            lock_key = lock_prefix + sid
            if lock_key in self._strings or lock_key in self._hashes:
                continue  # 锁 key 已出现(对话在跑)→ 跳过
            seen = False
            base = value
            if base.endswith(":seen"):
                seen = True
                base = base.removesuffix(":seen")
            m = re.match(r"^(.+):(\d+):\w+$", base) or re.match(
                r"^(.+):(\d+)$",
                base,
            )
            uid = m.group(1) if m else None
            ts = float(m.group(2)) if m else 0.0
            if seen or grace <= 0 or (now - ts) >= grace:
                del h[sid]
                self._strings["agentscope:running:global"] = (
                    self._strings.get("agentscope:running:global", 0) - 1
                )
                if uid:
                    user_key = f"agentscope:running:user:{uid}"
                    self._strings[user_key] = self._strings.get(user_key, 0) - 1
                cleaned += 1
        return cleaned


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()
