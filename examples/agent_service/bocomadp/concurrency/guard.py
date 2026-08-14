# -*- coding: utf-8 -*-
"""/chat 并发控制核心:Redis 原子占位 + 注册表 + 入口对账(折中版)。

设计要点(见 specs/2026-08-13-agent-service-concurrency-design.md):
- 入口 ``try_acquire`` 用 INCR 原子占位,超限回滚,天然无 TOCTOU 竞态;
- 注册表 Hash 记录 sid→``{user_id}:{ts}:{token}[:seen]``,token 用于唯一校验
  删除,避免同 session 重注册后旧对账误删新条目;``:seen`` 为"锁出现过"
  标志(Redis 共享,多实例可见);
- 对账移到请求入口(每次触发,无限频):锁 key 已消失 且 超过 grace 的条目,
  经 Lua 校验当前值仍为观察值才 HDEL 并 DECR(原子判定,多实例安全);
- 启动时 ``reconcile_on_startup`` 重建计数(以注册表为准,吸收残留漂移);
- 一次性伴随观察任务:register 时启动,持续观察本对话锁 key 是否出现;
  锁出现 → 给注册表 value 追加 ``:seen``(Redis 共享,确认对话真在跑,
  结束后对账立即清理,免 grace),锁从未出现(装配失败)→ 不标,
  交给对账+grace 兜底。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable

from agentscope.app.message_bus import MessageBusKeys

logger = logging.getLogger("bocomadp.concurrency.guard")

_GLOBAL_KEY = "agentscope:running:global"
_SESSIONS_KEY = "agentscope:running:sessions"

# 对账 Lua(整体原子,1 次往返):读注册表 → 判锁 → 判定候选(seen 立即 /
# grace 兜底)→ 原子清理 + 减计数。单脚本原子执行期间无其他命令(register)
# 可插入,因此无需 token 校验(旧 _CHECK_AND_DELETE_LUA 的"HGET==观察值"
# 在单脚本内恒真);同 session 重注册保护由"grace/seen 判定"天然提供
# (新值无 seen 且在 grace 内 → 跳过不清理)。删条目 + DECR global +
# DECR user 同脚本原子,消除清理窗口。
_RECONCILE_LUA = """
-- 对账 Lua:读注册表 → 判锁 → 判定候选(seen 立即 / grace 兜底)→ 原子清理+减计数
-- KEYS[1]=注册表, ARGV[1]=锁key前缀, ARGV[2]=grace_secs, ARGV[3]=now
local SESSIONS = KEYS[1]
local LOCK_PREFIX = ARGV[1]
local grace = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local entries = redis.call('HGETALL', SESSIONS)
local cleaned = 0
for i = 1, #entries, 2 do
    local sid = entries[i]
    local value = entries[i + 1]
    if redis.call('EXISTS', LOCK_PREFIX .. sid) == 0 then
        local seen = false
        local base = value
        if string.sub(value, -#':seen') == ':seen' then
            seen = true
            base = string.sub(value, 1, -#':seen' - 1)
        end
        local uid, ts_str = string.match(base, '^(.+):(%d+):%w+$')
        if not uid then
            uid, ts_str = string.match(base, '^(.+):(%d+)$')
        end
        local ts = uid and tonumber(ts_str) or 0
        if seen or grace <= 0 or (now - ts) >= grace then
            redis.call('HDEL', SESSIONS, sid)
            redis.call('DECR', 'agentscope:running:global')
            if uid then
                redis.call('DECR', 'agentscope:running:user:' .. uid)
            end
            cleaned = cleaned + 1
        end
    end
end
return cleaned
"""

# 条件注册:同 sid 覆盖写时,若旧对话已结束(锁 key 消失)→ 先释放旧名额再写入;
# 旧对话仍在跑 → 直接覆盖(同 session 并发已被框架 409 挡住,走到这里属放行后的
# 快速续跑,覆盖安全)。防止同 session 快速重注册导致计数单向累积。
# uid 提取前先剥掉旧 value 的 :seen 后缀,保证带标志的旧条目也能正确释放
# 每用户计数。
_REGISTER_LUA = """
-- KEYS[1]=注册表, ARGV[1]=sid, ARGV[2]=新value, ARGV[3]=锁key前缀
local old = redis.call('HGET', KEYS[1], ARGV[1])
if old then
    local lock_key = ARGV[3] .. ARGV[1]
    if redis.call('EXISTS', lock_key) == 0 then
        -- 旧条目存在且旧对话已结束(锁消失)→ 释放旧名额
        redis.call('HDEL', KEYS[1], ARGV[1])
        redis.call('DECR', 'agentscope:running:global')
        local base = string.gsub(old, ':seen$', '')
        local uid = string.match(base, '^(.+):%d+:%w+$') or string.match(base, '^(.+):%d+$')
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
_SEEN_SUFFIX = ":seen"


def _user_key(user_id: str) -> str:
    return f"agentscope:running:user:{user_id}"


def _parse_registered(value: str) -> tuple[str, float, str]:
    """从注册表 value 拆出 ``(user_id, 注册时间戳, token)``。

    新格式为 ``{user_id}:{ts}:{token}``,可带 ``:seen`` 后缀;兼容旧格式
    ``{user_id}:{ts}``(token 为空串);格式完全不符的条目视为 ``ts=now``
    (保守:在 grace>0 时跳过不清理),避免误回收。
    """
    try:
        base = value.removesuffix(_SEEN_SUFFIX)
        parts = base.rsplit(":", 2)
        user_id = parts[0]
        ts = float(parts[1])
        token = parts[2] if len(parts) == 3 else ""
        return user_id, ts, token
    except (ValueError, TypeError):
        return value, time.time(), ""


class ConcurrencyGuard:
    """Redis-backed concurrency limiter for the /chat endpoint.

    ``redis_provider`` 是惰性客户端提供器:每命令前调用一次,返回
    redis 客户端(鸭子类型,需 incr/decr/hset/hget/hdel/hgetall/exists/
    eval/set)。惰性是为了兼容连接池由框架 lifespan 创建的现实
    (get_client() 在进入 context 前不可用),以及测试注入 FakeRedis。
    """

    def __init__(
        self,
        redis_provider: Callable[[], Any],
        *,
        max_running: int = 10,
        max_running_per_user: int = 3,
        watch_interval: float = 1.0,
        watch_timeout: float = 90.0,
    ) -> None:
        self._redis_provider = redis_provider
        self._max_running = max_running
        self._max_running_per_user = max_running_per_user
        self._watch_interval = watch_interval
        self._watch_timeout = watch_timeout
        # 一次性伴随观察任务跟踪(测试清理用)。
        self._watch_tasks: set[asyncio.Task] = set()

    @property
    def _redis(self):
        return self._redis_provider()

    async def try_acquire(self, user_id: str) -> bool:
        """原子占位:先用户后全局双维度,超限即回滚返回 False。

        顺序先用户后全局:用户超限时只回滚自己的用户计数、不碰全局计数,
        避免"某用户超限请求临时占用全局名额"导致其他用户被全局上限误拒
        (并发下 4 请求本应 2 成功 2 失败,错误顺序会退化成 1 成功 3 失败)。
        """
        redis = self._redis
        cur_user = await redis.incr(_user_key(user_id))
        if self._max_running_per_user > 0 and cur_user > self._max_running_per_user:
            await redis.decr(_user_key(user_id))
            return False
        cur_global = await redis.incr(_GLOBAL_KEY)
        if self._max_running > 0 and cur_global > self._max_running:
            await redis.decr(_GLOBAL_KEY)
            await redis.decr(_user_key(user_id))
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
        self._spawn_watch(session_id)

    def _spawn_watch(self, session_id: str) -> None:
        """为对话启动一次性观察任务:锁 key 出现即标 :seen。"""
        task = asyncio.create_task(self._watch_lock(session_id))
        self._watch_tasks.add(task)
        task.add_done_callback(self._watch_tasks.discard)

    async def _watch_lock(self, session_id: str) -> None:
        """一次性伴随任务:观察本对话锁 key 是否出现(最长 watch_timeout)。

        出现 → 给注册表 value 追加 ``:seen``(Redis 共享,多实例可见;
        对话确实在跑,结束后对账立即清理),使命完成;超时未出现
        (装配失败/进程内异常)→ 不标,交给对账+grace 兜底。
        """
        try:
            deadline = time.monotonic() + self._watch_timeout
            while time.monotonic() < deadline:
                if await self._redis.exists(
                    MessageBusKeys.session_lock(session_id),
                ):
                    value = await self._redis.hget(_SESSIONS_KEY, session_id)
                    if value and not value.endswith(_SEEN_SUFFIX):
                        await self._redis.hset(
                            _SESSIONS_KEY,
                            session_id,
                            value + _SEEN_SUFFIX,
                        )
                    return
                await asyncio.sleep(self._watch_interval)
        except Exception:  # noqa: BLE001 — fail-open:观察失败不影响任何路径
            logger.exception("concurrency watch task failed for %r", session_id)

    async def close(self) -> None:
        """取消并等待全部观察任务(进程关闭/测试清理用)。"""
        tasks = list(self._watch_tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def rollback(self, session_id: str, user_id: str) -> None:
        """释放名额(非 2xx 响应 / 注册失败时调用)。"""
        redis = self._redis
        await redis.decr(_GLOBAL_KEY)
        await redis.decr(_user_key(user_id))
        await redis.hdel(_SESSIONS_KEY, session_id)

    async def reconcile(self, grace_secs: float = 0.0) -> int:
        """入口对账一轮(整体 Lua 化,1 次往返):释放过期对话名额。

        单脚本原子执行:读注册表 → 判锁 → 判定候选(锁消失 + seen → 立即;
        无 seen + 过 grace → 清理)→ HDEL + DECR global + DECR user 原子。
        脚本执行期间无并发插入,故无需 token 校验(见 _RECONCILE_LUA 注释);
        多实例安全、天然幂等;``grace_secs`` 跳过注册后不久(框架锁 key
        尚未创建)的条目,防止装配窗口内误回收导致计数单向漂移。
        """
        redis = self._redis
        cleaned = await redis.eval(
            _RECONCILE_LUA,
            1,
            _SESSIONS_KEY,
            _LOCK_KEY_PREFIX,
            grace_secs,
            time.time(),
        )
        return int(cleaned or 0)

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
