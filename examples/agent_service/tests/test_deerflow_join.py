"""join 已结束 run 的快速收尾测试（deerflow_chat.py join_run_stream）。

背景：SDK 在 sessionStorage 残留 ``lg:stream:{threadId}`` 时，挂载即
join 上次 run；若该 run 已结束（记账清理 / Redis log 覆盖），旧实现会
进入 live 阶段空等心跳帧，SSE 连接永不关闭，前端 ``isStreaming``
卡死（“请等待当前响应完成”）。本文件验证修复：已确认结束的 run
回放未遇 end 时立即产出 ``event: end`` 并关闭连接。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.app.message_bus import InMemoryMessageBus

from bocomadp.deerflow.bridge import BusBridge
from bocomadp.deerflow.protocol import (
    END_SENTINEL,
    EVENT_MESSAGES,
    EVENT_METADATA,
)
from bocomadp.deerflow.routers.deerflow_chat import deerflow_router
from bocomadp.deerflow.runs import RunManager, RunStatus

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


class FakeStorage:
    """get_session 恒返回非 None（仅 join 路径的 404 校验需要）。"""

    async def get_session(self, user_id: str, agent_id: str, session_id: str):
        return {}


class FakeChatService:
    """interrupt 幂等（join 收尾时 finally 会调用，异常被路由层吞掉）。"""

    async def interrupt(self, user_id: str, session_id: str, agent_id: str):
        return None


class FakeChatRunRegistry:
    """session → asyncio.Task 的简化替身；None 表示无活跃任务。"""

    def __init__(self, task=None) -> None:
        self._task = task

    def get(self, session_id: str):
        return self._task


def _make_app(
    run_manager: RunManager,
    bus: InMemoryMessageBus,
    registry: FakeChatRunRegistry,
) -> FastAPI:
    # 与 main.py 一致：router 挂到子应用，再 mount 到 /api（对外路径不变）；
    # state 必须设在子应用上（挂载后 request.app 是子应用）。
    api = FastAPI()
    api.state.run_manager = run_manager
    api.state.bus_bridge = BusBridge(bus)
    api.state.chat_service = FakeChatService()
    api.state.chat_run_registry = registry
    api.state.storage = FakeStorage()
    api.include_router(deerflow_router)
    app = FastAPI()
    app.mount("/api", api)
    return app


def test_join_finished_record_ends_immediately() -> None:
    """记账落定终态的 run：回放无 end 也立即返回 event: end。"""
    mgr = RunManager()
    rec = mgr.create_or_reject("default", "t1", "a1")
    mgr.mark_finished(rec.run_id, RunStatus.SUCCESS)
    app = _make_app(mgr, InMemoryMessageBus(), FakeChatRunRegistry())

    with TestClient(app) as client:
        response = client.get(
            f"/api/deerflow/threads/t1/runs/{rec.run_id}/stream")

    assert response.status_code == 200
    assert "event: end" in response.text


def test_join_unknown_run_without_active_task_ends() -> None:
    """run 记账已清理且原生无活跃任务（页面刷新 join 残留 run）：
    立即收尾，不再 live 空等。"""
    mgr = RunManager()  # 无任何记录（模拟已被清理）
    app = _make_app(mgr, InMemoryMessageBus(), FakeChatRunRegistry(None))

    with TestClient(app) as client:
        response = client.get(
            "/api/deerflow/threads/t1/runs/ghost-run/stream")

    assert response.status_code == 200
    assert "event: end" in response.text


def test_join_unknown_run_with_active_task_still_waits() -> None:
    """原生链路有活跃任务时不得收尾：连接保持，run 结束广播后自然关闭。

    全异步单事件循环驱动（InMemoryMessageBus 的 pub/sub 队列绑定创建
    时的 loop，跨 loop 发布事件会静默丢失；生产用 RedisMessageBus 无
    此限制）。task/bus 必须在 loop 内创建：Python 3.12 中
    ``asyncio.Future()`` 在无当前 event loop 的主线程会抛
    ``RuntimeError``（前序测试残留 loop 状态时必现）。
    """
    import asyncio

    import httpx

    from agentscope.app._bus_ops import publish_session_event

    mgr = RunManager()

    async def scenario() -> str:
        task = asyncio.Future()  # 未完成 → 活跃
        bus = InMemoryMessageBus()
        app = _make_app(mgr, bus, FakeChatRunRegistry(task))
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test") as client:
                resp_task = asyncio.create_task(
                    client.get("/api/deerflow/threads/t1/runs/ghost-run/stream"))
                # 0.3 秒内不应返回（live 阶段挂起，只可能有心跳帧）
                await asyncio.sleep(0.3)
                assert not resp_task.done()
                # 广播本 run 的 end 事件（模拟后台任务真正结束）→ 自然关闭
                await publish_session_event(
                    bus, "t1", REPLY_END, run_id="ghost-run")
                response = await asyncio.wait_for(resp_task, timeout=2.0)
                return response.text
        finally:
            task.cancel()

    body = asyncio.run(scenario())
    assert "event: end" in body


def test_bridge_run_finished_after_empty_replay() -> None:
    """bridge 层：回放为空 + run_finished=True → 立即产出 end 哨兵。"""
    import asyncio

    bus = InMemoryMessageBus()
    bridge = BusBridge(bus)

    async def scenario():
        out = []
        agen = bridge.subscribe_run("t1", "run1", run_finished=True)
        async for evt in agen:
            out.append(evt)
        return out

    evts = asyncio.run(scenario())
    assert evts == [END_SENTINEL]


def test_bridge_run_finished_still_replays_log() -> None:
    """bridge 层：run_finished=True 不回放为空的完整日志仍按序交付。"""
    import asyncio

    from agentscope.app._bus_ops import publish_session_event

    bus = InMemoryMessageBus()
    asyncio.run(publish_session_event(bus, "t1", REPLY_START, run_id="run1"))
    asyncio.run(publish_session_event(bus, "t1", TEXT_DELTA, run_id="run1"))
    asyncio.run(publish_session_event(bus, "t1", REPLY_END, run_id="run1"))
    bridge = BusBridge(bus)

    async def scenario():
        out = []
        agen = bridge.subscribe_run("t1", "run1", run_finished=True)
        async for evt in agen:
            out.append(evt)
        return out

    evts = asyncio.run(scenario())
    assert [e.event for e in evts] == [EVENT_METADATA, EVENT_MESSAGES, "__end__"]
    assert evts[0] is not END_SENTINEL
