"""DeerFlow SSE 帧序列化单测（protocol.py）。

对齐 deer-flow 2.0 ``format_sse`` 协议事实：
- field 顺序 event → data → id（可选）→ 空行
- 心跳 ``: heartbeat\\n\\n``（纯注释帧）
- 结束 ``event: end\\ndata: null\\n\\n``
"""

from __future__ import annotations

from bocomadp.deerflow.protocol import (
    END_SENTINEL,
    EVENT_CUSTOM,
    EVENT_END,
    EVENT_MESSAGES,
    EVENT_METADATA,
    HEARTBEAT_SENTINEL,
    StreamEvent,
    format_sse,
    with_event_id,
)


def test_format_sse_field_order() -> None:
    """event → data → id → 空行（LangGraph SDK 解码顺序）。"""
    evt = StreamEvent(
        id="1-0",
        event=EVENT_MESSAGES,
        data=[{"role": "assistant", "content": "hi"}, {"langgraph_node": "agent"}],
    )
    lines = format_sse(evt).split("\n")
    assert lines[0] == "event: messages"
    assert lines[1].startswith("data: ")
    assert lines[2] == "id: 1-0"
    assert lines[3] == ""
    assert lines[4] == ""


def test_format_sse_without_id() -> None:
    """无 id 时省略 id 行（保持两空行结尾）。"""
    evt = StreamEvent(id="", event=EVENT_METADATA, data={"run_id": "r"})
    lines = format_sse(evt).split("\n")
    assert lines[0] == "event: metadata"
    assert lines[1].startswith("data: ")
    assert lines[2] == ""
    assert lines[3] == ""


def test_format_sse_unicode() -> None:
    """中文内容保持 UTF-8（ensure_ascii=False）。"""
    evt = StreamEvent(id="", event=EVENT_MESSAGES, data=[{"role": "assistant", "content": "你好"}])
    assert "你好" in format_sse(evt)


def test_heartbeat_sentinel() -> None:
    """心跳为纯注释帧。"""
    assert format_sse(HEARTBEAT_SENTINEL) == ": heartbeat\n\n"


def test_end_sentinel() -> None:
    """结束帧带 data: null（对齐 deer-flow format_sse("end", None)）。"""
    assert format_sse(END_SENTINEL) == "event: end\ndata: null\n\n"


def test_end_sentinel_constant() -> None:
    """END_SENTINEL 的协议事件名为 end（供路由层识别）。"""
    assert END_SENTINEL.event == "__end__"


def test_with_event_id_fills_id() -> None:
    """bridge 用 entry_id 补全事件 id。"""
    evt = StreamEvent(id="", event=EVENT_CUSTOM, data={"type": "on_tool_call"})
    filled = with_event_id(evt, "3-0")
    assert filled.id == "3-0"
    assert filled.event == EVENT_CUSTOM
    # 原对象不变（frozen dataclass + replace）
    assert evt.id == ""


def test_with_event_id_keeps_sentinels() -> None:
    """哨兵事件 id 保持为空（不覆盖特殊语义）。"""
    assert with_event_id(HEARTBEAT_SENTINEL, "3-0") is HEARTBEAT_SENTINEL
    assert with_event_id(END_SENTINEL, "3-0") is END_SENTINEL
