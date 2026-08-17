# -*- coding: utf-8 -*-
"""DeerFlow 风格 SSE 适配包（bocomadp/deerflow）。

基于原生 chat 链路（``ChatService`` + ``MessageBus``）新增的 deer-flow 2.0
（LangGraph Platform）风格 SSE 路由适配层：

- :mod:`~bocomadp.deerflow.protocol`   协议数据类与帧序列化（唯一接触帧格式的文件）
- :mod:`~bocomadp.deerflow.formatter`  AgentEvent dict → StreamEvent 翻译
  （输入/输出侧统一对齐 deer-flow 协议）
- :mod:`~bocomadp.deerflow.bridge`     MessageBus 薄适配（回放 + 订阅过滤 + Last-Event-ID 游标）
- :mod:`~bocomadp.deerflow.runs`       RunManager（run_id 生成、session↔run 映射、状态推断）
- :mod:`~bocomadp.deerflow.deps`       FastAPI 依赖注入
- :mod:`~bocomadp.deerflow.routers`    路由层（threads/runs 资源模型）

执行引擎、缓冲回放、并发控制、取消能力全部复用原生实现；本包只做协议
翻译与路由编排，不重复实现任何运行时能力。
"""

from __future__ import annotations

from .bridge import BusBridge
from .formatter import DeerflowSSEFormatter
from .protocol import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    EVENT_CUSTOM,
    EVENT_END,
    EVENT_ERROR,
    EVENT_MESSAGES,
    EVENT_METADATA,
    StreamEvent,
    format_sse,
)
from .runs import RunManager, RunRecord, RunStatus

__all__ = [
    "BusBridge",
    "DeerflowSSEFormatter",
    "RunManager",
    "RunRecord",
    "RunStatus",
    "StreamEvent",
    "format_sse",
    "EVENT_METADATA",
    "EVENT_MESSAGES",
    "EVENT_CUSTOM",
    "EVENT_ERROR",
    "EVENT_END",
    "HEARTBEAT_SENTINEL",
    "END_SENTINEL",
]
