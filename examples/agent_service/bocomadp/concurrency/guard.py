# -*- coding: utf-8 -*-
"""/chat 并发控制核心:Redis 原子占位 + 注册表 + 入口对账(折中版)。

设计要点(见 specs/2026-08-13-agent-service-concurrency-design.md):
- 入口 ``try_acquire`` 用 INCR 原子占位,超限回滚,天然无 TOCTOU 竞态;
- 注册表 Hash 记录 sid→``{user_id}:{ts}:{token}``,token 用于唯一校验删除,
  避免同 session 重注册后旧对账误删新条目;
- 对账移到请求入口(每次触发,无限频):锁 key 已消失 且 超过 grace 的条目,
  经 Lua 校验当前值仍为观察值才 HDEL 并 DECR(原子判定,多实例安全);
- 启动时 ``reconcile_on_startup`` 重建计数(以注册表为准,吸收残留漂移)。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from agentscope.app.message_bus import MessageBusKeys

_GLOBAL_KEY = "agentscope:running:global"
_SESSIONS_KEY = "agentscope:running:sessions"

# 唯一校验删除:当前值仍等于观察值 且 锁 key 仍不存在 才删,否则说明该条目
# 已被并发重注册(同 session 新对话)接管或对话已开始运行(锁 key 出现),
# 跳过,防止误删新条目/运行中条目。锁检查与删除同脚本原子,消除
# "应用层 EXISTS 与 Lua 删除之间锁 key 恰好出现"的 TOCTOU 窗口。
_CHECK_AND_DELETE_LUA = """
-- KEYS[1]=注册表, ARGV[1]=sid, ARGV[2]=观察值, ARGV[3]=锁key前缀
local v = redis.call('HGET', KEYS[1], ARGV[1])
if v == ARGV[2] and redis.call('EXISTS', ARGV[3] .. ARGV[1]) == 0 then
    redis.call('HDEL', KEYS[1], ARGV[1])
    return 1
end
return 0
"""

# 条件注册:同 sid 覆盖写时,若旧对话已结束(锁 key 消失)→ 先释放旧名额再写入;
# 旧对话仍在跑 → 直接覆盖(同 session 并发已被框架 409 挡住,走到这里属放行后的
# 快速续跑,覆盖安全)。防止同 session 快速重注册导致计数单向累积。
_REGISTER_LUA = """
-- KEYS[1]=注册表, ARGV[1]=sid, ARGV[2]=新value, ARGV[3]=锁key前缀
local old = redis.call('HGET', KEYS[1], ARGV[1])
if old then
    local lock_key = ARGV[3] .. ARGV[1]
    if redis.call('EXISTS', lock_key) == 0 then
        -- 旧条目存在且旧对话已结束(锁消失)→ 释放旧名额
        redis.call('HDEL', KEYS[1], ARGV[1])
        redis.call('DECR', 'agentscope:running:global')
        local uid = string.match(old, '^(.+):%d+:%w+$') or string.match(old, '^(.+):%d+$')
        if uid then
            redis.call('DECR', 'agentscope:running:user:' .. uid)
        end
        return 1   -- 释放了旧名额
    end
    -- 旧对话仍在跑:直接覆盖(框架 409 会阻止同 session 并发,正常不会到这)
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
return 0
"""

_LOCK_KEY_PREFIX = "agentscope:session:lock:"


def _user_key(user_id: str) -> str:
    return f"agentscope:running:user:{user_id}"


def _parse_registered(value: str) -> tuple[str, float, str]:
    """从注册表 value 拆出 ``(user_id, 注册时间戳, token)``。

    新格式为 ``{user_id}:{ts}:{token}``;兼容旧格式 ``{user_id}:{ts}``
    (token 为空串);格式完全不符的条目视为 ``ts=now``(保守:在
    grace>0 时跳过不清理),避免误回收。
    """
    try:
        parts = value.rsplit(":", 2)
        user_id = parts[0]
        ts = float(parts[1])
        token = parts[2] if len(parts) == 3 else ""
        return user_id, ts, token
    except (ValueError, TypeError):
        return value, time.time(), ""


class ConcurrencyGuard:
    """Redis-backed concurrency limiter for the /chat endpoint.

    ``redis_provider`` 是惰性客户端提供器:每命令前调用一次,返回
    redis 客户端(鸭子类型,需 incr/decr/hset/hdel/hgetall/exists/eval/set)。
    惰性是为了兼容连接池由框架 lifespan 创建的现实(get_client()
    在进入 context 前不可用),以及测试注入 FakeRedis。
    """

    def __init__(
        self,
        redis_provider: Callable[[], Any],
        *,
        max_running: int = 10,
        max_running_per_user: int = 3,
    ) -> None:
        self._redis_provider = redis_provider
        self._max_running = max_running
        self._max_running_per_user = max_running_per_user
        # 本实例"亲眼见过锁 key 出现过"的 sid 集合(进程内存)。
        # 用途:区分"锁出现过又消失 = 对话确实结束(立即清理,免 grace)"
        # 与"锁从未出现过 = 可能还在装配(grace 兜底)",消除正常对话结束后
        # 的 grace 误拒窗口。多实例各自维护:见过锁的实例负责及时清理,
        # 没见过的实例靠 grace 兜底,HDEL 幂等保证谁清理都正确。
        self._seen: set[str] = set()

    @property
    def _redis(self):
        return self._redis_provider()

    async def try_acquire(self, user_id: str) -> bool:
        """原子占位:全局与用户双维度,超限即回滚返回 False。"""
        redis = self._redis
        cur_global = await redis.incr(_GLOBAL_KEY)
        if self._max_running > 0 and cur_global > self._max_running:
            await redis.decr(_GLOBAL_KEY)
            return False
        cur_user = await redis.incr(_user_key(user_id))
        if self._max_running_per_user > 0 and cur_user > self._max_running_per_user:
            await redis.decr(_user_key(user_id))
            await redis.decr(_GLOBAL_KEY)
            return False
        return True

    async def register(self, session_id: str, user_id: str) -> None:
        """记录 sid→``{user_id}:{ts}:{token}``;同 sid 覆盖时条件释放旧名额。

        旧条目存在且旧对话锁 key 已消失(旧对话真结束)→ Lua 内先释放旧
        名额再写入新条目,防止同 session 快速重注册导致计数单向累积。
        """
        token = uuid.uuid4().hex[:8]
        value = f"{user_id}:{int(time.time())}:{token}"
        await self._redis.eval(
            _REGISTER_LUA,
            1,
            _SESSIONS_KEY,
            session_id,
            value,
            _LOCK_KEY_PREFIX,
        )

    async def rollback(self, session_id: str, user_id: str) -> None:
        """释放名额(非 2xx 响应 / 注册失败时调用)。"""
        redis = self._redis
        await redis.decr(_GLOBAL_KEY)
        await redis.decr(_user_key(user_id))
        await redis.hdel(_SESSIONS_KEY, session_id)

    async def reconcile(self, grace_secs: float = 0.0) -> int:
        """入口对账一轮:释放"锁 key 已消失 且 超过注册宽限期"的对话名额。

        对每个候选条目用唯一 Lua 校验删除(观察值 == 当前值才删),返回 1 者
        才 DECR,天然幂等、多实例安全;``grace_secs`` 跳过注册后不久(框架
        锁 key 尚未创建)的条目,防止装配窗口内误回收导致计数单向漂移。
        """
        redis = self._redis
        entries = await redis.hgetall(_SESSIONS_KEY)
        candidates: list[tuple[str, str, str]] = []  # (sid, value, user_id)
        for session_id, value in entries.items():
            lock_key = MessageBusKeys.session_lock(session_id)
            if await redis.exists(lock_key):
                # 锁存在 → 对话在跑;记住"见过锁",结束后即可立即清理
                self._seen.add(session_id)
                continue
            if session_id in self._seen:
                # 锁出现过又消失 = 对话确实结束 → 立即清理,不等 grace
                # (正常对话零延迟释放,消除 grace 的 6s 误拒窗口)
                user_id, _ts, _token = _parse_registered(value)
                candidates.append((session_id, value, user_id))
                continue
            # 从未见过锁 → 可能还在装配(或装配失败)→ grace 兜底
            user_id, registered_at, _token = _parse_registered(value)
            if grace_secs > 0 and time.time() - registered_at < grace_secs:
                continue
            candidates.append((session_id, value, user_id))
        if not candidates:
            return 0

        pipe = redis.pipeline()
        for session_id, value, _user_id in candidates:
            pipe.eval(
                _CHECK_AND_DELETE_LUA,
                1,
                _SESSIONS_KEY,
                session_id,
                value,
                _LOCK_KEY_PREFIX,
            )
        results = await pipe.execute()

        cleaned = 0
        for result, (session_id, value, user_id) in zip(results, candidates):
            if result:
                self._seen.discard(session_id)
                await redis.decr(_GLOBAL_KEY)
                await redis.decr(_user_key(user_id))
                cleaned += 1
        return cleaned

    async def reconcile_on_startup(self) -> None:
        """以注册表为唯一事实源重建计数(启动时执行一次)。

        吸收实例残留漂移(如上次进程异常退出遗留的计数);多实例下各
        自重建后由入口 INCR/DECR 原子配对保证后续一致。
        """
        redis = self._redis
        entries = await redis.hgetall(_SESSIONS_KEY)
        await redis.set(_GLOBAL_KEY, len(entries))
        by_user: dict[str, int] = {}
        for value in entries.values():
            user_id, _ts, _token = _parse_registered(value)
            by_user[user_id] = by_user.get(user_id, 0) + 1
        for user_id, count in by_user.items():
            await redis.set(_user_key(user_id), count)
