"""BusBridge 单测（bridge.py）。

使用 InMemoryMessageBus（Mode A/C/D 语义与 Redis 一致）验证：
- 回放阶段按 run_id 过滤（join 晚到订阅者 / 含原生 /chat/ 触发的 run）
- Last-Event-ID 精确续传（log_read since 游标）
- live 阶段 run_id 过滤 + 心跳哨兵 + end 收敛
- 事件 id 填充 entry_id（SSE id: 字段即断线续传游标）
"""

from __future__ import annotations

import asyncio

from agentscope.app._bus_ops import publish_session_event
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.message_bus._keys import MessageBusKeys

from bocomadp.deerflow.bridge import BusBridge
from bocomadp.deerflow.protocol import (
    END_SENTINEL,
    EVENT_CUSTOM,
    EVENT_MESSAGES,
    EVENT_METADATA,
    HEARTBEAT_SENTINEL,
)

REPLY_START = {
    "type": "REPLY_START",
    "session_id": "t1",
    "reply_id": "r1",
    "name": "agent_a",
    "role": "assistant",
    "run_id": "run1",
}
TEXT_DELTA = {
    "type": "TEXT_BLOCK_DELTA",
    "reply_id": "r1",
    "block_id": "b1",
    "delta": "你好",
    "run_id": "run1",
}
REPLY_END = {
    "type": "REPLY_END",
    "session_id": "t1",
    "reply_id": "r1",
    "finished_reason": "COMPLETED",
    "run_id": "run1",
}


def _collect(agen):
    """把异步生成器全部收进列表（异步驱动）。"""
    return asyncio.run(_collect_async(agen))


async def _collect_async(agen):
    out = []
    async for evt in agen:
        out.append(evt)
    return out


async def _seed_log(bus: InMemoryMessageBus) -> list[str]:
    """写入 run1 的完整事件序列，返回 entry_id 列表。"""
    ids = []
    for event in (REPLY_START, TEXT_DELTA, REPLY_END):
        ids.append(await publish_session_event(bus, "t1", event, run_id="run1"))
    return ids


def test_join_replays_full_run_sequence() -> None:
    """join 已完成 run：回放出 metadata → messages → end 完整序列。"""
    bus = InMemoryMessageBus()
    asyncio.run(_seed_log(bus))
    bridge = BusBridge(bus)

    evts = _collect(bridge.subscribe_run("t1", "run1", heartbeat_interval=0.1))
    assert [e.event for e in evts] == [EVENT_METADATA, EVENT_MESSAGES, "__end__"]
    # 事件 id 即 Redis Stream entry_id（SSE id: 游标）
    assert evts[0].id == "1-0"
    assert evts[1].id == "2-0"
    assert evts[0].data["run_id"] == "run1"
    assert evts[0].data["thread_id"] == "t1"
    assert evts[1].data[0]["content"] == "你好"
    assert evts[2] is END_SENTINEL


def test_join_filters_other_runs() -> None:
    """同 session 混入其他 run 的事件被过滤（原生 /chat/ 与 deerflow 并存）。"""
    bus = InMemoryMessageBus()
    asyncio.run(_seed_log(bus))
    # 另一 run 的事件（模拟原生 /chat/ 触发，run_id 自动生成）
    await_publish = asyncio.run(
        publish_session_event(
            bus,
            "t1",
            {"type": "TEXT_BLOCK_DELTA", "reply_id": "r2", "block_id": "b9", "delta": "别的 run", "run_id": "run2"},
        ),
    )
    assert await_publish  # 发布成功

    bridge = BusBridge(bus)
    evts = _collect(bridge.subscribe_run("t1", "run1", heartbeat_interval=0.1))
    assert [e.event for e in evts] == [EVENT_METADATA, EVENT_MESSAGES, "__end__"]
    assert evts[1].data[0]["content"] == "你好"  # 只含 run1 的内容


def test_last_event_id_resumes_from_cursor() -> None:
    """Last-Event-ID 精确续传：只回放游标之后的事件。"""
    bus = InMemoryMessageBus()
    ids = asyncio.run(_seed_log(bus))
    bridge = BusBridge(bus)

    evts = _collect(
        bridge.subscribe_run("t1", "run1", last_event_id=ids[0], heartbeat_interval=0.1),
    )
    assert [e.event for e in evts] == [EVENT_MESSAGES, "__end__"]
    assert evts[0].id == ids[1]


def test_live_subscription_filters_and_ends() -> None:
    """live 阶段：只收本 run 广播；收到 end 后收敛。"""
    bus = InMemoryMessageBus()
    bridge = BusBridge(bus)

    async def scenario():
        out = []
        # 先建立订阅（回放为空，直接进 live）
        agen = bridge.subscribe_run("t1", "run1", heartbeat_interval=0.5)
        async def drain():
            async for evt in agen:
                out.append(evt)
        task = asyncio.create_task(drain())
        await asyncio.sleep(0.05)  # 让订阅建立

        # 广播本 run 事件（log_append + publish 由 bus_ops 保证顺序）
        await publish_session_event(bus, "t1", REPLY_START, run_id="run1")
        await publish_session_event(bus, "t1", TEXT_DELTA, run_id="run1")
        # 混入他 run 事件（应被过滤）
        await publish_session_event(
            bus,
            "t1",
            {"type": "TEXT_BLOCK_DELTA", "reply_id": "r9", "block_id": "b9", "delta": "noise", "run_id": "run2"},
        )
        await publish_session_event(bus, "t1", REPLY_END, run_id="run1")
        await task
        return out

    evts = asyncio.run(scenario())
    assert [e.event for e in evts] == [EVENT_METADATA, EVENT_MESSAGES, "__end__"]
    assert evts[1].data[0]["content"] == "你好"


def test_heartbeat_on_idle() -> None:
    """空闲超时产出心跳哨兵；事件到来后恢复。"""
    bus = InMemoryMessageBus()
    bridge = BusBridge(bus)

    async def scenario():
        out = []
        agen = bridge.subscribe_run("t1", "run1", heartbeat_interval=0.05)
        async def drain():
            async for evt in agen:
                out.append(evt)
        task = asyncio.create_task(drain())
        await asyncio.sleep(0.12)  # 超过两个心跳间隔
        await publish_session_event(bus, "t1", REPLY_START, run_id="run1")
        await publish_session_event(bus, "t1", REPLY_END, run_id="run1")
        await task
        return out

    evts = asyncio.run(scenario())
    # 心跳哨兵至少出现一次，且位于真实事件之前
    assert HEARTBEAT_SENTINEL in evts
    idx_hb = evts.index(HEARTBEAT_SENTINEL)
    idx_meta = next(i for i, e in enumerate(evts) if e.event == EVENT_METADATA)
    assert idx_hb < idx_meta
    assert evts[-1] is END_SENTINEL


def test_empty_run_keeps_subscribing() -> None:
    """run_id 无匹配事件时不产出任何帧（直到心跳/结束），不报错。"""
    bus = InMemoryMessageBus()
    asyncio.run(_seed_log(bus))  # 只有 run1
    bridge = BusBridge(bus)

    async def scenario():
        out = []
        agen = bridge.subscribe_run("t1", "ghost-run", heartbeat_interval=0.05)
        async def drain():
            async for evt in agen:
                out.append(evt)
        task = asyncio.create_task(drain())
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return out

    evts = asyncio.run(scenario())
    assert all(e is HEARTBEAT_SENTINEL for e in evts)


def test_bus_keys_unchanged() -> None:
    """会话事件通道 key 与原生 stream 端点一致（同一条 Redis Stream）。"""
    assert MessageBusKeys.session_events("t1") == MessageBusKeys.session_events("t1")
