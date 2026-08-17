# -*- coding: utf-8 -*-
"""MessageBus 薄适配：按 run 过滤的回放 + 订阅（BusBridge）。

不实现任何缓冲——直接复用原生 Redis Stream replay log（容量
``SESSION_REPLAY_MAX_LEN`` = 1000，大于 deer-flow 的 256）与 pub/sub
广播。Redis Stream entry_id 即 SSE ``id:`` 游标：``Last-Event-ID``
断线续传 = ``log_read(since=...)`` 精确定位，零自研。

每个订阅者独占一个 :class:`DeerflowSSEFormatter` 实例（其累积状态如
tool call arguments 不跨订阅者共享）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from agentscope.app.message_bus._keys import MessageBusKeys

from .formatter import DeerflowSSEFormatter
from .protocol import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    StreamEvent,
    with_event_id,
)

if TYPE_CHECKING:
    from agentscope.app.message_bus._base import MessageBus


class BusBridge:
    """薄封装 MessageBus 的 session 事件通道，按 run_id 过滤事件流。"""

    def __init__(self, message_bus: "MessageBus") -> None:
        """Wrap the application message bus.

        Args:
            message_bus (`MessageBus`):
                The application bus (Redis Stream + pub/sub).
        """
        self._bus = message_bus

    async def subscribe_run(
        self,
        session_id: str,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
        run_finished: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield this run's events: replay first, then live.

        两阶段，全部按 ``run_id`` 字段过滤：

        1. **回放**：``log_read(since=last_event_id)``——无游标从头读
           （join 晚到订阅者），有游标精确续传（断线重连）。
        2. **live**：``subscribe`` 广播，空闲超过 *heartbeat_interval*
           秒产出 :data:`HEARTBEAT_SENTINEL` 保持连接。

        ``run_finished`` 供 join 路径传入"run 已确认结束"（调用方从
        RunManager / 原生注册表推断）：回放未遇 end 时直接收尾，避免
        进入 live 空等——已结束 run 不会再有任何事件，仅心跳帧会让
        SSE 连接永不关闭，前端 ``isStreaming`` 卡死（
        “请等待当前响应完成”）。

        事件 id 填 Redis Stream entry_id（回放来自 log 条目，live 来自
        payload 的 ``_entry_id`` 字段），供客户端 ``Last-Event-ID``
        回传续传。产生 :data:`END_SENTINEL` 后迭代结束。

        Args:
            session_id (`str`):
                Thread id（== 原生 session id）。
            run_id (`str`):
                目标 run 标识；与 payload 的 ``run_id`` 字段比对过滤。
            last_event_id (`str | None`, optional):
                ``Last-Event-ID`` 请求头值；精确续传游标。
            heartbeat_interval (`float`, optional):
                空闲心跳间隔（秒），对齐 deer-flow 默认 15s。

        Yields:
            `StreamEvent`:
                已翻译的协议事件（id 已填充）；哨兵见模块说明。
        """
        formatter = DeerflowSSEFormatter()
        key = MessageBusKeys.session_events(session_id)

        # ── 1. Replay：log_read(since) 精确续传 / 从头回放 ─────────────
        # Last-Event-ID 只认 "<seq>-0" 格式游标（数字开头）；空串、"-" 等
        # 非法值一律视为从头回放，避免 InMemory 版 log_read 解析崩溃。
        since = None
        if last_event_id:
            head = last_event_id.split("-", 1)[0]
            since = last_event_id if head.isdigit() else None
        for entry_id, payload in await self._bus.log_read(
            key,
            since=since,
            max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
        ):
            if payload.get("run_id") != run_id:
                continue
            for evt in formatter.translate(payload):
                yield with_event_id(evt, entry_id)
                if evt is END_SENTINEL:
                    # run 已在回放中结束（join 已完成 run）：不再订阅 live
                    return

        # ── 1.5 已结束 run 快速收尾 ───────────────────────────────────
        # run 已确认结束且回放未遇 end（终态事件已被 Redis Stream 容量
        # 滚动覆盖 / 记账记录已清理）：该 run 的事件流已终结，live 阶段
        # 只会产出心跳帧——连接永不关闭，前端 isStreaming 卡死。
        if run_finished:
            yield END_SENTINEL
            return

        # ── 2. Live：feeder + queue + wait_for 心跳（对齐原生 stream
        #          端点模式，见 _router/_session.py::_sse_generator）───
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def _feeder() -> None:
            """读广播并转发到队列；end of stream 时放入 None 哨兵。"""
            try:
                async for payload in self._bus.subscribe(key):
                    if payload.get("run_id") != run_id:
                        continue
                    await queue.put(payload)
            except asyncio.CancelledError:
                pass
            finally:
                await queue.put(None)

        feeder_task = asyncio.create_task(
            _feeder(),
            name=f"deerflow-bridge:{session_id}:{run_id}",
        )
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=heartbeat_interval,
                    )
                except asyncio.TimeoutError:
                    yield HEARTBEAT_SENTINEL
                    continue
                if payload is None:
                    return
                entry_id = str(payload.get("_entry_id", ""))
                for evt in formatter.translate(payload):
                    if entry_id:
                        # 有 entry_id 才发 id 帧；无 id 时保持原样，避免空 id
                        # 清空客户端 lastEventId，导致回传空 Last-Event-ID
                        yield with_event_id(evt, entry_id)
                    else:
                        yield evt
                    if evt is END_SENTINEL:
                        return
        finally:
            feeder_task.cancel()
            try:
                await feeder_task
            except asyncio.CancelledError:
                pass


__all__ = ["BusBridge"]
