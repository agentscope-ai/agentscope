# -*- coding: utf-8 -*-
"""DeerFlow 风格 SSE 协议原语（唯一接触"帧格式"的文件）。

对齐 deer-flow 2.0 源码协议事实：

- 帧格式（``backend/app/gateway/services.py::format_sse``）：field 顺序为
  ``event:`` → ``data:`` → ``id:``（可选）→ 空行，被 LangGraph Platform 生态
  （``useStream`` React Hook / ``langgraph-sdk`` SSE decoder）直接消费。
- 心跳（``: heartbeat\\n\\n``）：纯注释帧，防止代理/浏览器超时断连。
- 结束（``event: end``）：流终止哨兵，data 为 ``null``。
- 事件枚举（``backend/packages/harness/deerflow/runtime/stream_bridge/base.py``）：
  ``metadata`` / ``updates`` / ``messages`` / ``custom`` / ``error`` / ``end``。

本模块只定义数据类与序列化；事件翻译在 :mod:`formatter`，缓冲与游标在
:mod:`bridge`，互不越界。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

# ── 事件名常量（对齐 deer-flow StreamEvent.event）──────────────────────
EVENT_METADATA = "metadata"
"""Run 元信息首帧（run_id / thread_id / assistant_id）。"""

EVENT_MESSAGES = "messages"
"""消息增量帧，data 为 ``[chunk, metadata]`` 元组。"""

EVENT_CUSTOM = "custom"
"""自定义事件帧（工具调用/工具结果/HITL 确认等）。"""

EVENT_ERROR = "error"
"""错误帧，data 为 ``{"message", "name"}``。"""

EVENT_END = "end"
"""结束哨兵帧，data 为 ``None``。"""


@dataclass(frozen=True)
class StreamEvent:
    """单条流事件（协议面，未序列化）。

    Attributes:
        id:
            单调递增的事件 ID，作为 SSE ``id:`` 字段；支持 ``Last-Event-ID``
            断线续传。本方案直接复用 Redis Stream entry_id（``{ms}-{seq}``）。
        event:
            SSE 事件名，见模块级 ``EVENT_*`` 常量。
        data:
            JSON 可序列化载荷。
    """

    id: str
    event: str
    data: Any


HEARTBEAT_SENTINEL = StreamEvent(id="", event="__heartbeat__", data=None)
"""心跳哨兵：订阅循环在空闲超时后产出，序列化为 ``: heartbeat\\n\\n``。"""

END_SENTINEL = StreamEvent(id="", event="__end__", data=None)
"""结束哨兵：订阅循环在收到终止事件后产出，序列化为 ``event: end``。"""


def format_sse(evt: StreamEvent) -> str:
    """将单条 StreamEvent 序列化为一个完整 SSE 帧。

    Field 顺序：``event:`` → ``data:`` → ``id:``（可选）→ 空行。心跳哨兵
    （``event == "__heartbeat__"``）序列化为纯注释帧。
    """
    if evt is HEARTBEAT_SENTINEL:
        return ": heartbeat\n\n"
    if evt is END_SENTINEL:
        # 对齐 deer-flow sse_consumer：``format_sse("end", None)`` 产出
        # ``event: end\ndata: null\n\n``（LangGraph SDK 以此识别流终止）。
        return "event: end\ndata: null\n\n"

    data = json.dumps(evt.data, default=str, ensure_ascii=False)
    parts = [f"event: {evt.event}", f"data: {data}"]
    if evt.id:
        parts.append(f"id: {evt.id}")
    parts.append("")
    parts.append("")
    return "\n".join(parts)


def with_event_id(evt: StreamEvent, event_id: str) -> StreamEvent:
    """为事件补上 entry_id（供 bridge 在回放/订阅产出时调用）。

    哨兵事件（``__heartbeat__``/``__end__``）原样返回，避免 replace 副本
    破坏 :func:`format_sse` 的身份特判；已有 id 的事件保留原 id（防覆盖）。
    """
    if evt.event.startswith("__") or evt.id:
        return evt
    return replace(evt, id=event_id)


__all__ = [
    "EVENT_METADATA",
    "EVENT_MESSAGES",
    "EVENT_CUSTOM",
    "EVENT_ERROR",
    "EVENT_END",
    "StreamEvent",
    "HEARTBEAT_SENTINEL",
    "END_SENTINEL",
    "format_sse",
    "with_event_id",
]
