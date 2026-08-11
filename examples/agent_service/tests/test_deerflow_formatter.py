"""DeerflowSSEFormatter 事件翻译单测（formatter.py）。

覆盖方案决策⑥映射表全部六类映射 + 未知事件透传兜底。输入为总线上的
AgentEvent dict（``model_dump(mode="json")`` 形态，type 为大写枚举串）。
"""

from __future__ import annotations

from bocomadp.deerflow.formatter import DeerflowSSEFormatter
from bocomadp.deerflow.protocol import (
    END_SENTINEL,
    EVENT_CUSTOM,
    EVENT_ERROR,
    EVENT_MESSAGES,
    EVENT_METADATA,
)


def _reply_start(run_id: str = "run1", session_id: str = "t1") -> dict:
    return {
        "type": "REPLY_START",
        "session_id": session_id,
        "reply_id": "r1",
        "name": "agent_a",
        "role": "assistant",
        "run_id": run_id,
    }


def _reply_end(finished_reason: str = "COMPLETED", error: dict | None = None) -> dict:
    return {
        "type": "REPLY_END",
        "session_id": "t1",
        "reply_id": "r1",
        "finished_reason": finished_reason,
        "error": error,
        "run_id": "run1",
    }


# ── metadata 首帧 ─────────────────────────────────────────────────────


def test_reply_start_emits_metadata_once() -> None:
    f = DeerflowSSEFormatter()
    evts = f.translate(_reply_start())
    assert len(evts) == 1
    assert evts[0].event == EVENT_METADATA
    assert evts[0].data["run_id"] == "run1"
    assert evts[0].data["thread_id"] == "t1"
    assert evts[0].data["assistant_id"] == "agent_a"
    assert evts[0].data["reply_id"] == "r1"
    # 后续事件不再重复 metadata
    assert f.translate(_reply_start()) == []


# ── messages 增量 ─────────────────────────────────────────────────────


def test_text_delta_maps_to_messages() -> None:
    f = DeerflowSSEFormatter()
    evts = f.translate(
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "block_id": "b1", "delta": "你好", "run_id": "run1"},
    )
    assert len(evts) == 1
    assert evts[0].event == EVENT_MESSAGES
    # chunk 对齐 LangGraph 消息 tuple 协议：type/id 必填（langgraph-sdk
    # MessageTupleManager 缺 type 崩溃、缺 id 忽略），id 用 reply_id 聚合
    assert evts[0].data == [
        {"type": "ai", "content": "你好", "id": "r1"},
        {"langgraph_node": "agent"},
    ]


def test_text_block_start_end_map_to_messages() -> None:
    f = DeerflowSSEFormatter()
    start = f.translate({"type": "TEXT_BLOCK_START", "reply_id": "r1", "block_id": "b1", "run_id": "run1"})
    end = f.translate({"type": "TEXT_BLOCK_END", "reply_id": "r1", "block_id": "b1", "run_id": "run1"})
    assert start[0].event == EVENT_MESSAGES and start[0].data[0]["content"] == ""
    assert end[0].event == EVENT_MESSAGES and end[0].data[0]["content"] == ""
    assert start[0].data[0]["type"] == "ai" and start[0].data[0]["id"] == "r1"
    assert end[0].data[0]["type"] == "ai" and end[0].data[0]["id"] == "r1"


def test_thinking_delta_carries_reasoning_flag() -> None:
    f = DeerflowSSEFormatter()
    evts = f.translate(
        {"type": "THINKING_BLOCK_DELTA", "reply_id": "r1", "block_id": "b1", "delta": "推理中", "run_id": "run1"},
    )
    assert evts[0].event == EVENT_MESSAGES
    assert evts[0].data[1] == {"langgraph_node": "agent", "reasoning": True}
    # thinking 增量进 additional_kwargs.reasoning_content（对齐 deer-flow
    # 官方 patched deepseek/mimo 语义），content 留空避免与正文混淆
    assert evts[0].data[0]["type"] == "ai"
    assert evts[0].data[0]["content"] == ""
    assert evts[0].data[0]["id"] == "r1"
    assert evts[0].data[0]["additional_kwargs"] == {"reasoning_content": "推理中"}


# ── custom：工具调用（arguments 跨事件累积）──────────────────────────


def test_tool_call_accumulates_arguments() -> None:
    f = DeerflowSSEFormatter()
    start = f.translate(
        {"type": "TOOL_CALL_START", "reply_id": "r1", "tool_call_id": "c1", "tool_call_name": "get_balance", "run_id": "run1"},
    )
    assert start[0].event == EVENT_CUSTOM
    assert start[0].data == {"type": "on_tool_call", "name": "get_balance", "arguments": ""}

    delta = f.translate(
        {"type": "TOOL_CALL_DELTA", "reply_id": "r1", "tool_call_id": "c1", "delta": '{"ac', "run_id": "run1"},
    )
    assert delta[0].data["arguments"] == '{"ac'

    end = f.translate({"type": "TOOL_CALL_END", "reply_id": "r1", "tool_call_id": "c1", "run_id": "run1"})
    assert end[0].data["arguments"] == '{"ac'


# ── custom：工具结果（result 文本跨事件累积）─────────────────────────


def test_tool_result_accumulates_text() -> None:
    f = DeerflowSSEFormatter()
    start = f.translate(
        {"type": "TOOL_RESULT_START", "reply_id": "r1", "tool_call_id": "c1", "tool_call_name": "get_balance", "run_id": "run1"},
    )
    assert start[0].event == EVENT_CUSTOM
    assert start[0].data == {"type": "on_tool_end", "name": "get_balance", "result": ""}

    d1 = f.translate(
        {"type": "TOOL_RESULT_TEXT_DELTA", "reply_id": "r1", "tool_call_id": "c1", "delta": "余额", "run_id": "run1"},
    )
    d2 = f.translate(
        {"type": "TOOL_RESULT_TEXT_DELTA", "reply_id": "r1", "tool_call_id": "c1", "delta": "100 元", "run_id": "run1"},
    )
    assert d1[0].data["result"] == "余额"
    assert d2[0].data["result"] == "余额100 元"

    end = f.translate({"type": "TOOL_RESULT_END", "reply_id": "r1", "tool_call_id": "c1", "state": "SUCCESS", "run_id": "run1"})
    assert end[0].data["result"] == "余额100 元"


# ── custom：HITL / 自定义事件 ────────────────────────────────────────


def test_require_user_confirm_maps_to_custom() -> None:
    f = DeerflowSSEFormatter()
    evts = f.translate(
        {
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": "r1",
            "tool_calls": [{"id": "c1", "name": "get_balance"}],
            "run_id": "run1",
        },
    )
    # 前端不认识 custom on_require_confirm，但 custom 事件保留；
    # 尾部 end 哨兵使本轮 SSE 在卡片帧后收尾（park 无 ReplyEndEvent，
    # 否则连接永不关闭、前端 isStreaming 卡死）
    custom = evts[-2]
    assert evts[-1] is END_SENTINEL
    assert custom.event == EVENT_CUSTOM
    assert custom.data["type"] == "on_require_confirm"
    assert custom.data["reply_id"] == "r1"
    assert custom.data["tool_calls"] == [{"id": "c1", "name": "get_balance"}]


def test_require_user_confirm_emits_human_input_card() -> None:
    """RequireUserConfirmEvent → tool 消息帧（human_input 确认卡片）。

    前端 SDK 将 messages 帧并入 values.messages，HumanInputCard 据此
    渲染确认卡片；此测试验证 chunk 结构与 artifact.human_input 载荷
    可被前端 parseHumanInputRequest 解析。
    """
    f = DeerflowSSEFormatter()
    evts = f.translate(
        {
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": "r1",
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "Bash",
                    "input": '{"command": "mkdir -p /tmp/demo", "description": "建目录"}',
                    "state": "asking",
                },
            ],
            "run_id": "run1",
        },
    )
    # 首条为 messages 帧（tool 消息），次为 custom 事件，尾部 end 哨兵
    msg_evt = evts[0]
    assert msg_evt.event == EVENT_MESSAGES
    chunk, metadata = msg_evt.data
    assert metadata["langgraph_node"] == "agent"
    assert chunk["type"] == "tool"
    # 前端 isClarificationToolMessage 只认该 name 才会渲染确认卡片
    assert chunk["name"] == "ask_clarification"
    assert chunk["tool_call_id"] == "c1"
    assert chunk["id"] == "confirm-c1"
    human_input = chunk["artifact"]["human_input"]
    assert human_input["kind"] == "human_input_request"
    assert human_input["source"] == "agent_scope_permission"
    assert human_input["request_id"] == "confirm-c1"
    assert human_input["input_mode"] == "single_choice"
    assert "mkdir -p /tmp/demo" in human_input["question"]
    values = [o["value"] for o in human_input["options"]]
    assert values == ["confirm", "reject"]
    assert len(evts) == 3
    assert evts[-1] is END_SENTINEL


def test_require_user_confirm_adds_always_allow_option() -> None:
    """携带 suggested_rules 时追加“同意并始终允许”选项。"""
    f = DeerflowSSEFormatter()
    evts = f.translate(
        {
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": "r1",
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "Bash",
                    "input": "{\"command\": \"mkdir -p /tmp/demo\"}",
                    "state": "asking",
                    "suggested_rules": [
                        {
                            "tool_name": "Bash",
                            "rule_content": "mkdir:*",
                            "behavior": "allow",
                            "source": "suggested",
                        },
                    ],
                },
            ],
            "run_id": "run1",
        },
    )
    msg_evt = evts[0]
    chunk, _metadata = msg_evt.data
    human_input = chunk["artifact"]["human_input"]
    values = [o["value"] for o in human_input["options"]]
    assert values == ["confirm", "reject", "confirm_always"]
    assert evts[-1] is END_SENTINEL


def test_custom_event_passthrough() -> None:
    f = DeerflowSSEFormatter()
    evts = f.translate({"type": "CUSTOM", "name": "state_updated", "value": {"k": 1}, "run_id": "run1"})
    assert evts[0].event == EVENT_CUSTOM
    assert evts[0].data == {"k": 1, "type": "state_updated"}


# ── end / error ───────────────────────────────────────────────────────


def test_reply_end_normal_emits_end_sentinel() -> None:
    f = DeerflowSSEFormatter()
    assert f.translate(_reply_end()) == [END_SENTINEL]


def test_reply_end_error_emits_error_then_end() -> None:
    f = DeerflowSSEFormatter()
    evts = f.translate(
        _reply_end(finished_reason="ERROR", error={"type": "MODEL_ERROR", "message": "boom"}),
    )
    assert len(evts) == 2
    assert evts[0].event == EVENT_ERROR
    assert evts[0].data == {"message": "boom", "name": "MODEL_ERROR"}
    assert evts[1] is END_SENTINEL


def test_reply_end_interrupted_emits_end_only() -> None:
    """cancel 后的 REPLY_END(INTERRUPTED) 只收敛为 end（不带 error）。"""
    f = DeerflowSSEFormatter()
    assert f.translate(_reply_end(finished_reason="INTERRUPTED")) == [END_SENTINEL]


# ── 未知事件兜底 ─────────────────────────────────────────────────────


def test_unknown_event_passthrough_as_custom() -> None:
    f = DeerflowSSEFormatter()
    evts = f.translate({"type": "SOME_FUTURE_EVENT", "foo": "bar", "run_id": "run1"})
    assert len(evts) == 1
    assert evts[0].event == EVENT_CUSTOM
    assert evts[0].data["type"] == "some_future_event"
    assert evts[0].data["foo"] == "bar"
    # 内部元字段不外泄
    assert "run_id" not in evts[0].data
