# -*- coding: utf-8 -*-
"""AgentEvent → StreamEvent 翻译器。

输入为消息总线上广播/回放的 AgentEvent dict（``model_dump(mode="json")``
产物，``type`` 为大写枚举字符串，payload 含 M2 附加的 ``run_id`` 字段），
输出为 deer-flow 2.0（LangGraph Platform）协议的 :class:`StreamEvent`。

翻译映射（方案决策⑥）：

| AgentEvent（原生）            | StreamEvent            | data 载荷                                   |
|-------------------------------|------------------------|---------------------------------------------|
| ``ReplyStartEvent``           | ``metadata``           | run_id / thread_id / assistant_id 首帧      |
| ``TextBlock*``                | ``messages``           | ``[{"type": "ai", "content", "id"}, metadata]`` |
| ``ThinkingBlock*``            | ``messages``           | chunk 附 ``additional_kwargs.reasoning_content``，metadata 附 ``reasoning: true`` |
| ``ToolCallStart/Delta/End``   | ``custom``             | ``{"type": "on_tool_call", ...}``           |
| ``ToolResultStart/Text/End``  | ``custom``             | ``{"type": "on_tool_end", ...}``            |
| ``RequireUserConfirmEvent``   | ``messages`` + ``custom`` | tool 消息帧（``artifact.human_input`` 确认卡片，前端 HumanInputCard 渲染）+ 原 ``on_require_confirm`` |
| ``CustomEvent``               | ``custom``             | 原样透传                                   |
| ``ReplyEndEvent(normal)``     | ``end``                | 哨兵（data=None）                          |
| ``ReplyEndEvent(error)``      | ``error`` + ``end``    | ``{"message", "name"}`` 后接哨兵           |
| 未知事件                      | ``custom``             | 原样透传而非丢弃 |

输入侧按 TEXT/THINKING/TOOL 分支匹配，输出侧统一翻译为 deer-flow 协议。
每个 run 一个实例。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .protocol import (
    END_SENTINEL,
    EVENT_CUSTOM,
    EVENT_END,
    EVENT_ERROR,
    EVENT_MESSAGES,
    EVENT_METADATA,
    StreamEvent,
)

logger = logging.getLogger(__name__)

# ── 事件类型常量（对齐 agentscope.event.EventType 字符串值）─────────────
_T_REPLY_START = "REPLY_START"
_T_REPLY_END = "REPLY_END"
_T_TEXT_BLOCK_START = "TEXT_BLOCK_START"
_T_TEXT_BLOCK_DELTA = "TEXT_BLOCK_DELTA"
_T_TEXT_BLOCK_END = "TEXT_BLOCK_END"
_T_THINKING_BLOCK_START = "THINKING_BLOCK_START"
_T_THINKING_BLOCK_DELTA = "THINKING_BLOCK_DELTA"
_T_THINKING_BLOCK_END = "THINKING_BLOCK_END"
_T_TOOL_CALL_START = "TOOL_CALL_START"
_T_TOOL_CALL_DELTA = "TOOL_CALL_DELTA"
_T_TOOL_CALL_END = "TOOL_CALL_END"
_T_TOOL_RESULT_START = "TOOL_RESULT_START"
_T_TOOL_RESULT_TEXT_DELTA = "TOOL_RESULT_TEXT_DELTA"
_T_TOOL_RESULT_END = "TOOL_RESULT_END"
_T_REQUIRE_USER_CONFIRM = "REQUIRE_USER_CONFIRM"
_T_CUSTOM = "CUSTOM"

# 确认卡片协议常量（对齐 deer-flow 前端 human_input_request）。
CONFIRM_SOURCE = "agent_scope_permission"
"""确认卡片的 source 标识（前端按 source 区分请求发起方）。"""


def _tool_call_summary(tool_call: dict) -> str:
    """从 tool_call dict 提取面向用户的确认摘要文本。

    优先展示 Bash 的 command / 文件工具的 path，解析失败时回退到原始
    input 字符串（截断 200 字符），保证卡片上总有可读内容。
    """
    name = str(tool_call.get("name", "tool"))
    raw_input = tool_call.get("input", "")
    detail = ""
    if raw_input:
        try:
            parsed = (
                json.loads(raw_input)
                if isinstance(raw_input, str)
                else raw_input
            )
            if isinstance(parsed, dict):
                for key in ("command", "path", "file_path"):
                    if parsed.get(key):
                        detail = str(parsed[key])
                        break
        except Exception:  # noqa: BLE001 —— 摘要尽力而为，失败回退原文
            detail = ""
    if not detail:
        detail = str(raw_input)[:200] if raw_input else "(no arguments)"
    return f"{name}: {detail}"


def build_confirm_card(tool_call: dict) -> dict[str, Any]:
    """构造前端 HumanInputCard 的 tool 消息 chunk。

    输入为 ``RequireUserConfirmEvent.tool_calls`` 中单条 tool_call 的
    ``model_dump(mode="json")`` 产物；输出对齐 deer-flow 前端
    ``extractHumanInputRequest`` 的解析协议（``type=tool`` +
    ``artifact.human_input``）。流式翻译与 threads 端点的刷新恢复
    共用本函数，保证两处卡片结构一致。
    """
    tool_call_id = str(tool_call.get("id", ""))
    request_id = f"confirm-{tool_call_id}"
    question = f"确认执行以下工具调用？\n\n{_tool_call_summary(tool_call)}"
    options = [
        {"id": "option-1", "label": "同意执行", "value": "confirm"},
        {"id": "option-2", "label": "拒绝", "value": "reject"},
    ]
    if tool_call.get("suggested_rules"):
        options.append(
            {
                "id": "option-3",
                "label": "同意并始终允许",
                "value": "confirm_always",
            },
        )
    return {
        "type": "tool",
        "id": request_id,
        # 前端 getMessageGroups 的 isClarificationToolMessage 只认
        # name == "ask_clarification" 的 tool 消息才会渲染 HumanInputCard
        # 组件；其他 name 会落为普通 tool 消息、只显示 content 文本。
        "name": "ask_clarification",
        "tool_call_id": tool_call_id,
        "content": question,
        "artifact": {
            "human_input": {
                "version": 1,
                "kind": "human_input_request",
                "source": CONFIRM_SOURCE,
                "request_id": request_id,
                "tool_call_id": tool_call_id,
                "question": question,
                "input_mode": "single_choice",
                "options": options,
            },
        },
    }


def _evt(event: dict, name: str, data: Any = None) -> StreamEvent:
    """构造一个待补 id 的 StreamEvent（id 由 bridge 填 entry_id）。"""
    return StreamEvent(id="", event=name, data=data)


class DeerflowSSEFormatter:
    """AgentEvent dict → StreamEvent 翻译器（每个 run 一个实例）。"""

    def __init__(self) -> None:
        # metadata 首帧只发一次（reply_start 位置天然对齐 deer-flow 首帧）
        self._metadata_sent = False
        # tool_call_id -> 累积 arguments（delta 片段拼接）
        self._tool_call_args: dict[str, str] = {}
        # tool_call_id -> 工具名（start 时记录，供 delta/end 复用）
        self._tool_call_names: dict[str, str] = {}
        # tool_call_id -> 累积 result 文本（tool_result_text_delta 拼接）
        self._tool_result_text: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def translate(self, event: dict) -> list[StreamEvent]:
        """翻译一条 bus 上的 AgentEvent dict。

        返回 0..N 条 StreamEvent；事件 id 为空串，由调用方（bridge）用
        Redis Stream entry_id 填充。未知事件原样透传为 ``custom`` 而非
        丢弃（保证翻译层单点不吞事件）。
        """
        evt_type = str(event.get("type", "")).upper()
        handler = getattr(self, f"_on_{evt_type.lower()}", None)
        if handler is not None:
            try:
                return handler(event)
            except Exception:  # noqa: BLE001 —— 翻译失败不中断流
                logger.exception(
                    "deerflow formatter: failed to translate %s, "
                    "falling back to passthrough",
                    evt_type,
                )
        # 未知事件：原样透传为 custom
        return self._passthrough(event)

    # ------------------------------------------------------------------
    # 各事件翻译（handler 命名 _on_<type.lower()>）
    # ------------------------------------------------------------------

    def _on_reply_start(self, event: dict) -> list[StreamEvent]:
        """ReplyStartEvent → metadata 首帧（run/thread/assistant 标识）。"""
        if self._metadata_sent:
            return []
        self._metadata_sent = True
        payload = {
            "run_id": event.get("run_id"),
            "thread_id": event.get("session_id"),
            "assistant_id": event.get("name"),
            "reply_id": event.get("reply_id"),
        }
        return [_evt(event, EVENT_METADATA, payload)]

    def _on_reply_end(self, event: dict) -> list[StreamEvent]:
        """ReplyEndEvent → error 帧（失败时）+ end 哨兵。"""
        finished_reason = str(event.get("finished_reason", "")).upper()
        if finished_reason == "ERROR":
            error = event.get("error") or {}
            error_frame = _evt(
                event,
                EVENT_ERROR,
                {
                    "message": error.get("message", "unknown error"),
                    "name": error.get("type", "UNKNOWN"),
                },
            )
            return [error_frame, END_SENTINEL]
        return [END_SENTINEL]

    def _messages_chunk(
        self,
        event: dict,
        content: str,
        *,
        reasoning: bool = False,
    ) -> StreamEvent:
        """构造 messages 增量帧 ``[chunk, metadata]``。

        chunk 对齐 LangGraph 消息 tuple 协议（deer-flow 官方
        ``{"type": "ai", "content": ..., "id": ...}``）：

        - ``type`` 必填：langgraph-sdk ``MessageTupleManager.add`` 首行
          即 ``serialized.type.endsWith("MessageChunk")``，缺失直接抛
          TypeError（前端报 "Cannot read properties of undefined"）。
        - ``id`` 必填：同函数无 id 时仅 warn 并忽略该 chunk，前端收不到
          消息；以 reply_id 聚合同一回复的增量块。
        - thinking 增量进 ``additional_kwargs.reasoning_content``
          （content 留空，避免与正文混淆），LangChain chunk concat 对
          字符串自动拼接，对齐 deer-flow 官方 reasoning 语义。
        """
        chunk: dict[str, Any] = {
            "type": "ai",
            "content": "" if reasoning else content,
            "id": event.get("reply_id"),
        }
        metadata: dict[str, Any] = {"langgraph_node": "agent"}
        if reasoning:
            chunk["additional_kwargs"] = {"reasoning_content": content}
            metadata["reasoning"] = True
        return _evt(event, EVENT_MESSAGES, [chunk, metadata])

    def _on_text_block_start(self, event: dict) -> list[StreamEvent]:
        return [self._messages_chunk(event, "")]

    def _on_text_block_delta(self, event: dict) -> list[StreamEvent]:
        return [self._messages_chunk(event, event.get("delta", "") or "")]

    def _on_text_block_end(self, event: dict) -> list[StreamEvent]:
        return [self._messages_chunk(event, "")]

    def _on_thinking_block_start(self, event: dict) -> list[StreamEvent]:
        return [self._messages_chunk(event, "", reasoning=True)]

    def _on_thinking_block_delta(self, event: dict) -> list[StreamEvent]:
        return [
            self._messages_chunk(
                event,
                event.get("delta", "") or "",
                reasoning=True,
            ),
        ]

    def _on_thinking_block_end(self, event: dict) -> list[StreamEvent]:
        return [self._messages_chunk(event, "", reasoning=True)]

    # ── 工具调用（arguments 片段跨事件累积）────────────────────────────

    def _on_tool_call_start(self, event: dict) -> list[StreamEvent]:
        call_id = str(event.get("tool_call_id", ""))
        name = str(event.get("tool_call_name", ""))
        self._tool_call_args[call_id] = ""
        self._tool_call_names[call_id] = name
        return [
            _evt(
                event,
                EVENT_CUSTOM,
                {"type": "on_tool_call", "name": name, "arguments": ""},
            ),
        ]

    def _on_tool_call_delta(self, event: dict) -> list[StreamEvent]:
        call_id = str(event.get("tool_call_id", ""))
        delta = event.get("delta", "") or ""
        self._tool_call_args[call_id] = (
            self._tool_call_args.get(call_id, "") + delta
        )
        return [
            _evt(
                event,
                EVENT_CUSTOM,
                {
                    "type": "on_tool_call",
                    "name": self._tool_call_names.get(call_id, ""),
                    "arguments": self._tool_call_args[call_id],
                },
            ),
        ]

    def _on_tool_call_end(self, event: dict) -> list[StreamEvent]:
        call_id = str(event.get("tool_call_id", ""))
        arguments = self._tool_call_args.get(call_id, "")
        return [
            _evt(
                event,
                EVENT_CUSTOM,
                {
                    "type": "on_tool_call",
                    "name": self._tool_call_names.get(call_id, ""),
                    "arguments": arguments,
                },
            ),
        ]

    # ── 工具结果（result 文本跨事件累积）───────────────────────────────

    def _on_tool_result_start(self, event: dict) -> list[StreamEvent]:
        call_id = str(event.get("tool_call_id", ""))
        name = str(event.get("tool_call_name", ""))
        self._tool_call_names[call_id] = name
        self._tool_result_text[call_id] = ""
        return [
            _evt(
                event,
                EVENT_CUSTOM,
                {"type": "on_tool_end", "name": name, "result": ""},
            ),
        ]

    def _on_tool_result_text_delta(self, event: dict) -> list[StreamEvent]:
        call_id = str(event.get("tool_call_id", ""))
        delta = event.get("delta", "") or ""
        self._tool_result_text[call_id] = (
            self._tool_result_text.get(call_id, "") + delta
        )
        return [
            _evt(
                event,
                EVENT_CUSTOM,
                {
                    "type": "on_tool_end",
                    "name": self._tool_call_names.get(call_id, ""),
                    "result": self._tool_result_text[call_id],
                },
            ),
        ]

    def _on_tool_result_end(self, event: dict) -> list[StreamEvent]:
        call_id = str(event.get("tool_call_id", ""))
        return [
            _evt(
                event,
                EVENT_CUSTOM,
                {
                    "type": "on_tool_end",
                    "name": self._tool_call_names.get(call_id, ""),
                    "result": self._tool_result_text.get(call_id, ""),
                },
            ),
        ]

    # ── HITL / 自定义 / 兜底 ───────────────────────────────────────────

    def _on_require_user_confirm(self, event: dict) -> list[StreamEvent]:
        """RequireUserConfirmEvent → tool 消息（human_input 卡片）+ custom。

        对齐 deer-flow 前端原生 HITL 协议：每条待确认工具调用翻译为一条
        ``messages`` 帧（``type=tool`` + ``artifact.human_input``），SDK
        将其并入 ``values.messages``，前端 HumanInputCard 自动渲染确认
        卡片（同意/拒绝，携带 suggested_rules 时追加“同意并始终允许”）。

        原 ``on_require_confirm`` custom 事件保留（调试与旧订阅方兼容）。
        """
        stream_events: list[StreamEvent] = []
        tool_calls = event.get("tool_calls") or []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            stream_events.append(
                _evt(
                    event,
                    EVENT_MESSAGES,
                    [
                        build_confirm_card(tool_call),
                        {"langgraph_node": "agent"},
                    ],
                ),
            )
        # 原 custom 事件保留（调试/兼容，置于 messages 帧之后）
        stream_events.append(
            _evt(
                event,
                EVENT_CUSTOM,
                {
                    "type": "on_require_confirm",
                    "reply_id": event.get("reply_id"),
                    "tool_calls": tool_calls,
                },
            ),
        )
        # HITL park 收尾：原生在等待确认时不会发出 ReplyEndEvent（reply
        # 尚未结束，等待 Case B 续跑），bridge 的 live 订阅永远等不到 end
        # 哨兵，SSE 连接只靠心跳帧空转不关闭，前端 ``isStreaming`` 一直
        # 卡死（确认卡片也因此 disabled）。此处补发 end 哨兵让本轮流在
        # 卡片帧后正常收尾，用户点击确认/拒绝后由前端发起新 run 续跑。
        stream_events.append(END_SENTINEL)
        return stream_events

    def _on_custom(self, event: dict) -> list[StreamEvent]:
        """CustomEvent → custom 原样透传（type 取事件 name）。"""
        value = dict(event.get("value") or {})
        value["type"] = event.get("name", "custom")
        return [_evt(event, EVENT_CUSTOM, value)]

    def _passthrough(self, event: dict) -> list[StreamEvent]:
        """未知事件：custom 原样透传（剥掉内部元字段）。"""
        logger.debug(
            "deerflow formatter: passthrough unhandled type=%s",
            event.get("type"),
        )
        payload = {
            k: v
            for k, v in event.items()
            if k not in ("type", "run_id", "_entry_id")
        }
        payload.setdefault("type", str(event.get("type", "unknown")).lower())
        return [_evt(event, EVENT_CUSTOM, payload)]


__all__ = ["DeerflowSSEFormatter", "build_confirm_card"]
