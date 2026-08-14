# -*- coding: utf-8 -*-
"""Tests for ConcurrencyGuard acquire / register / rollback / reconcile."""
import asyncio
import time

import pytest

from agentscope.app.message_bus import MessageBusKeys

from bocomadp.concurrency.guard import ConcurrencyGuard

_SESSIONS_KEY = "agentscope:running:sessions"


@pytest.fixture
def guard(fake_redis):
    guard = ConcurrencyGuard(
        lambda: fake_redis,
        max_running=2,
        max_running_per_user=1,
        watch_interval=0.01,
        watch_timeout=0.1,
    )
    yield guard
    asyncio.run(guard.close())


def test_acquire_global_limit(guard, fake_redis):
    async def _scenario() -> None:
        assert await guard.try_acquire("u1") is True
        assert await guard.try_acquire("u2") is True   # 第 2 个也过
        assert await guard.try_acquire("u3") is False  # 全局满(2)
        # 超限回滚后计数恢复为 2(u1+u2 各占一个)
        assert await fake_redis.get("agentscope:running:global") == "2"

    asyncio.run(_scenario())


def test_acquire_per_user_limit(guard, fake_redis):
    async def _scenario() -> None:
        assert await guard.try_acquire("u1") is True
        assert await guard.try_acquire("u1") is False  # 该用户满(1)
        assert await guard.try_acquire("u2") is True   # 其他用户不受影响

    asyncio.run(_scenario())


def test_rollback_releases_slots(guard):
    async def _scenario() -> None:
        assert await guard.try_acquire("u1") is True
        await guard.rollback("sid-1", "u1")
        assert await guard.try_acquire("u1") is True   # 名额已归还

    asyncio.run(_scenario())


def test_register_and_reconcile_release(guard, fake_redis):
    async def _scenario() -> None:
        assert await guard.try_acquire("u1") is True
        await guard.register("sid-1", "u1")
        # 锁 key 不存在(对话未启动/已结束)→ reconcile 清理
        cleaned = await guard.reconcile(grace_secs=0)
        assert cleaned == 1
        assert await guard.try_acquire("u1") is True   # 名额已释放

    asyncio.run(_scenario())


def test_reconcile_keeps_running_sessions(guard, fake_redis):
    async def _scenario() -> None:
        await guard.try_acquire("u1")
        await guard.register("sid-1", "u1")
        # 模拟框架锁 key 已写:reconcile 必须跳过
        await fake_redis.hset(MessageBusKeys.session_lock("sid-1"), "x", "1")
        cleaned = await guard.reconcile(grace_secs=0)
        assert cleaned == 0
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert "sid-1" in entries
        # 值带注册时间戳与 token:u1:<ts>:<token>,按 ":" 三次拆分
        value = entries["sid-1"]
        assert value.startswith("u1:")
        assert value.count(":") == 2

    asyncio.run(_scenario())


def test_reconcile_is_idempotent_across_duplicates(guard, fake_redis):
    async def _scenario() -> None:
        await guard.try_acquire("u1")
        await guard.register("sid-1", "u1")
        assert await guard.reconcile(grace_secs=0) == 1
        assert await guard.reconcile(grace_secs=0) == 0  # 第二次无事可做

    asyncio.run(_scenario())


def test_reconcile_respects_register_grace(guard, fake_redis):
    async def _scenario() -> None:
        # 手动构造旧条目(注册于 100s 前)→ 超过 grace,应清理
        await fake_redis.hset(
            _SESSIONS_KEY,
            "sid-old",
            f"u1:{int(time.time()) - 100}:tok1",
        )
        # 手动构造新条目(刚注册)→ 未满 grace,不应清理
        await fake_redis.hset(
            _SESSIONS_KEY,
            "sid-new",
            f"u1:{int(time.time())}:tok2",
        )
        cleaned = await guard.reconcile(grace_secs=60)
        assert cleaned == 1
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert "sid-new" in entries   # 新条目保留
        assert "sid-old" not in entries  # 旧条目已清理

    asyncio.run(_scenario())


def test_reconcile_respects_reregister_grace(guard, fake_redis):
    async def _scenario() -> None:
        await guard.try_acquire("u1")
        await guard.register("sid-1", "u1")
        # 同 session 重注册:覆盖为新值(无 :seen,ts 为当前时间)
        await fake_redis.hset(
            _SESSIONS_KEY,
            "sid-1",
            f"u1:{int(time.time())}:token2",
        )
        # 新值在 grace 内(无 seen)→ 不清理,条目保留
        cleaned = await guard.reconcile(grace_secs=60)
        assert cleaned == 0
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert "sid-1" in entries
        assert entries["sid-1"].count(":") == 2
        # grace 过后(grace_secs=0)→ 清理
        assert await guard.reconcile(grace_secs=0) == 1

    asyncio.run(_scenario())


def test_seen_releases_immediately_after_lock_disappears(guard, fake_redis):
    async def _scenario() -> None:
        await guard.try_acquire("u1")
        await guard.register("sid-1", "u1")
        lock_key = MessageBusKeys.session_lock("sid-1")

        # ① 对话运行:锁 key 出现 → watch 任务给注册表 value 标 :seen
        await fake_redis.hset(lock_key, "x", "1")
        for _ in range(50):
            value = await fake_redis.hget(_SESSIONS_KEY, "sid-1")
            if value and value.endswith(":seen"):
                break
            await asyncio.sleep(0.02)
        assert (await fake_redis.hget(_SESSIONS_KEY, "sid-1")).endswith(":seen")

        # ② 对话结束:锁 key 消失(框架 finally 删)→ 即使注册未满 grace
        #   (grace_secs=60 远大于注册年龄),因"见过锁"也立即清理
        await fake_redis.hdel(lock_key, "x")
        cleaned = await guard.reconcile(grace_secs=60)
        assert cleaned == 1
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert "sid-1" not in entries  # 条目已清理
        assert await guard.try_acquire("u1") is True  # 名额已释放

    asyncio.run(_scenario())


def test_seen_not_applied_to_never_locked(guard, fake_redis):
    async def _scenario() -> None:
        await guard.try_acquire("u1")
        await guard.register("sid-1", "u1")
        # 锁从未出现过(装配中/装配失败)→ value 无 :seen,仍受 grace 保护
        assert await guard.reconcile(grace_secs=60) == 0
        value = await fake_redis.hget(_SESSIONS_KEY, "sid-1")
        assert value is not None and not value.endswith(":seen")
        # grace 过后(模拟注册足够久)才清理
        assert await guard.reconcile(grace_secs=0) == 1

    asyncio.run(_scenario())


def test_reconcile_on_startup_rebuilds_counters(guard, fake_redis):
    async def _scenario() -> None:
        # 残留计数:global 被异常推到 5,但注册表只有 2 条
        for _ in range(5):
            await fake_redis.incr("agentscope:running:global")
        await fake_redis.hset(_SESSIONS_KEY, "s1", f"u1:{int(time.time())}:t1")
        await fake_redis.hset(_SESSIONS_KEY, "s2", f"u2:{int(time.time())}:t2")

        await guard.reconcile_on_startup()

        assert await fake_redis.get("agentscope:running:global") == "2"
        assert await fake_redis.get("agentscope:running:user:u1") == "1"
        assert await fake_redis.get("agentscope:running:user:u2") == "1"

    asyncio.run(_scenario())


def test_register_releases_overwritten_expired_slot(fake_redis):
    async def _scenario() -> None:
        guard = ConcurrencyGuard(
            lambda: fake_redis,
            max_running=2,
            max_running_per_user=2,
            watch_interval=0.01,
            watch_timeout=0.1,
        )
        assert await guard.try_acquire("u1") is True
        await guard.register("sid-1", "u1")   # 第一次注册,无旧条目 → 纯写入
        assert await fake_redis.get("agentscope:running:global") == "1"
        # 对话已结束(无锁 key),同 session 快速续跑
        assert await guard.try_acquire("u1") is True   # 第 2 个名额
        await guard.register("sid-1", "u1")   # 第二次注册 → 释放旧名额
        # 旧名额已释放:global 回落到 1(而不是 2),注册表只有新条目
        assert await fake_redis.get("agentscope:running:global") == "1"
        assert await fake_redis.get("agentscope:running:user:u1") == "1"
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert "sid-1" in entries
        assert entries["sid-1"].count(":") == 2
        await guard.close()

    asyncio.run(_scenario())


def test_register_keeps_running_slot(fake_redis):
    async def _scenario() -> None:
        guard = ConcurrencyGuard(
            lambda: fake_redis,
            max_running=2,
            max_running_per_user=2,
            watch_interval=0.01,
            watch_timeout=0.1,
        )
        assert await guard.try_acquire("u1") is True
        await guard.register("sid-1", "u1")
        # 模拟旧对话仍在跑:锁 key 已写
        await fake_redis.hset(MessageBusKeys.session_lock("sid-1"), "x", "1")
        assert await guard.try_acquire("u1") is True   # 第 2 个名额
        await guard.register("sid-1", "u1")   # 覆盖写,不释放旧名额(409 场景)
        assert await fake_redis.get("agentscope:running:global") == "2"
        assert await fake_redis.get("agentscope:running:user:u1") == "2"
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert "sid-1" in entries
        await guard.close()

    asyncio.run(_scenario())


def test_watch_task_marks_seen_when_lock_appears(guard, fake_redis):
    async def _scenario() -> None:
        await guard.try_acquire("u1")
        await guard.register("sid-1", "u1")
        value = await fake_redis.hget(_SESSIONS_KEY, "sid-1")
        assert value is not None and not value.endswith(":seen")

        # 模拟锁 key 出现(对话装配完成开始运行)
        await fake_redis.hset(MessageBusKeys.session_lock("sid-1"), "x", "1")
        # 等观察任务轮询到锁并给注册表 value 标 :seen
        for _ in range(50):
            value = await fake_redis.hget(_SESSIONS_KEY, "sid-1")
            if value and value.endswith(":seen"):
                break
            await asyncio.sleep(0.02)
        assert (await fake_redis.hget(_SESSIONS_KEY, "sid-1")).endswith(":seen")

        # 对话结束:锁消失 → "见过锁"免 grace,立即清理
        await fake_redis.hdel(MessageBusKeys.session_lock("sid-1"), "x")
        cleaned = await guard.reconcile(grace_secs=999)
        assert cleaned == 1
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert "sid-1" not in entries

    asyncio.run(_scenario())


def test_watch_task_times_out_without_lock(guard, fake_redis):
    async def _scenario() -> None:
        await guard.try_acquire("u1")
        await guard.register("sid-1", "u1")
        # 锁从未出现(装配失败)→ 观察任务超时退出,不误标 :seen
        await asyncio.sleep(0.15)   # > watch_timeout=0.1
        value = await fake_redis.hget(_SESSIONS_KEY, "sid-1")
        assert value is not None and not value.endswith(":seen")
        assert not guard._watch_tasks   # 观察任务已自然消亡

    asyncio.run(_scenario())


def test_seen_flag_persisted_in_redis(guard, fake_redis):
    async def _scenario() -> None:
        # 直接构造带 :seen 的 value(模拟对账读 Redis 而非内存)
        await fake_redis.hset(
            _SESSIONS_KEY,
            "sid-1",
            f"u1:{int(time.time()) - 100}:tok1:seen",
        )
        # 锁已消失 + value 带 :seen → 立即清理,免 grace
        cleaned = await guard.reconcile(grace_secs=999)
        assert cleaned == 1
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert "sid-1" not in entries

    asyncio.run(_scenario())


def test_reconcile_delete_and_decr_atomic(guard, fake_redis):
    async def _scenario() -> None:
        assert await guard.try_acquire("u1") is True
        assert await guard.try_acquire("u2") is True
        await guard.register("sid-1", "u1")
        await guard.register("sid-2", "u2")
        assert await fake_redis.get("agentscope:running:global") == "2"

        # 锁均不存在(对话结束)→ reconcile 一次性清理两条
        cleaned = await guard.reconcile(grace_secs=0)
        assert cleaned == 2
        # 删条目与减计数原子完成(Lua 内 HDEL+DECR):计数与注册表一致(0)
        assert await fake_redis.get("agentscope:running:global") == "0"
        assert await fake_redis.get("agentscope:running:user:u1") == "0"
        assert await fake_redis.get("agentscope:running:user:u2") == "0"
        entries = await fake_redis.hgetall(_SESSIONS_KEY)
        assert entries == {}

    asyncio.run(_scenario())
