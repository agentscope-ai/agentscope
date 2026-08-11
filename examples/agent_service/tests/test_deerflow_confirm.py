# -*- coding: utf-8 -*-
"""确认机制双向翻译单测（formatter 请求翻译 + deerflow_chat 响应翻译）。

覆盖：

- ``_extract_human_input_response`` / ``_convert_input``：前端确认卡片
  应答（human 消息 ``additional_kwargs.human_input_response``）的识别；
- ``_build_user_confirm_event``：应答 → ``UserConfirmResultEvent`` 的
  request_id 匹配与 value 映射（confirm / reject / confirm_always）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agentscope.message import Msg, ToolCallBlock, ToolCallState
from agentscope.permission import PermissionBehavior, PermissionRule
from agentscope.state import AgentState

from bocomadp.deerflow.routers.deerflow_chat import (
    _HumanInputResponseMarker,
    _build_user_confirm_event,
    _convert_input,
    _extract_human_input_response,
)

USER_ID = "user-1"
AGENT_ID = "agent-a"
SESSION_ID = "t1"
AGENT_NAME = "agent_a"


def _confirm_response(value: str = "confirm") -> dict:
    """构造前端 HumanInputCard 应答载荷。"""
    return {
        "version": 1,
        "kind": "human_input_response",
        "source": "agent_scope_permission",
        "request_id": "confirm-c1",
        "response_kind": "option",
        "option_id": "option-1",
        "value": value,
    }


def _human_message(response: dict) -> dict:
    """构造前端提交的 hide_from_ui human 消息。"""
    return {
        "type": "human",
        "content": [
            {"type": "text", "text": "For your clarification, my answer is: confirm"}
        ],
        "additional_kwargs": {
            "hide_from_ui": True,
            "human_input_response": response,
        },
    }


def _asking_state(*, suggested_rules: list | None = None) -> AgentState:
    """构造含一条 ASKING 状态 tool_call 的 agent state。"""
    tool_call = ToolCallBlock(
        id="c1",
        name="Bash",
        input='{"command": "mkdir -p /tmp/demo"}',
        state=ToolCallState.ASKING,
        suggested_rules=suggested_rules or [],
    )
    state = AgentState()
    state.reply_id = "r1"
    state.context = [
        Msg(
            name=AGENT_NAME,
            role="assistant",
            content=[tool_call],
        ),
    ]
    return state


def _suggested_rule() -> PermissionRule:
    return PermissionRule(
        tool_name="Bash",
        rule_content="mkdir:*",
        behavior=PermissionBehavior.ALLOW,
        source="suggested",
    )


class _FakeStorage:
    """``_build_user_confirm_event`` 的 storage 依赖最小实现。"""

    def __init__(self, state: AgentState | None) -> None:
        self._state = state

    async def get_session(self, user_id, agent_id, session_id):
        if self._state is None:
            return None
        return SimpleNamespace(state=self._state)

    async def get_agent(self, user_id, agent_id):
        return SimpleNamespace(data=SimpleNamespace(name=AGENT_NAME))


# ── 应答识别（_extract_human_input_response / _convert_input）──────────


def test_extract_response_from_human_message() -> None:
    response = _confirm_response()
    assert _extract_human_input_response(_human_message(response)) == response


def test_extract_ignores_non_human_messages() -> None:
    ai_message = {"type": "ai", "content": "hello"}
    assert _extract_human_input_response(ai_message) is None
    human_no_kwargs = {"type": "human", "content": "hello"}
    assert _extract_human_input_response(human_no_kwargs) is None
    human_other_kwargs = {
        "type": "human",
        "content": "hello",
        "additional_kwargs": {"hide_from_ui": True},
    }
    assert _extract_human_input_response(human_other_kwargs) is None


def test_convert_input_detects_single_message_marker() -> None:
    response = _confirm_response()
    converted = _convert_input(_human_message(response))
    assert isinstance(converted, _HumanInputResponseMarker)
    assert converted.response == response


def test_convert_input_detects_message_list_marker() -> None:
    response = _confirm_response()
    converted = _convert_input({"messages": [_human_message(response)]})
    assert isinstance(converted, _HumanInputResponseMarker)
    assert converted.response == response


def test_convert_input_passes_through_plain_messages() -> None:
    converted = _convert_input(
        {"messages": [{"type": "human", "content": "你好"}]},
    )
    assert isinstance(converted, list)
    assert converted[0].role == "user"


# ── 应答 → UserConfirmResultEvent（_build_user_confirm_event）─────────


def test_confirm_value_maps_to_confirmed() -> None:
    state = _asking_state()
    storage = _FakeStorage(state)
    event = asyncio.run(
        _build_user_confirm_event(
            storage,
            USER_ID,
            AGENT_ID,
            SESSION_ID,
            _confirm_response("confirm"),
        ),
    )
    assert event is not None
    assert event.reply_id == "r1"
    assert len(event.confirm_results) == 1
    result = event.confirm_results[0]
    assert result.confirmed is True
    assert result.tool_call.id == "c1"
    assert result.rules is None


def test_reject_value_maps_to_denied() -> None:
    state = _asking_state()
    storage = _FakeStorage(state)
    event = asyncio.run(
        _build_user_confirm_event(
            storage,
            USER_ID,
            AGENT_ID,
            SESSION_ID,
            _confirm_response("reject"),
        ),
    )
    assert event is not None
    result = event.confirm_results[0]
    assert result.confirmed is False


def test_confirm_always_passes_suggested_rules() -> None:
    rule = _suggested_rule()
    state = _asking_state(suggested_rules=[rule])
    storage = _FakeStorage(state)
    event = asyncio.run(
        _build_user_confirm_event(
            storage,
            USER_ID,
            AGENT_ID,
            SESSION_ID,
            _confirm_response("confirm_always"),
        ),
    )
    assert event is not None
    result = event.confirm_results[0]
    assert result.confirmed is True
    assert result.rules is not None
    assert len(result.rules) == 1
    assert result.rules[0].rule_content == "mkdir:*"


def test_unmatched_request_id_returns_none() -> None:
    state = _asking_state()
    storage = _FakeStorage(state)
    response = _confirm_response("confirm")
    response["request_id"] = "confirm-unknown"
    event = asyncio.run(
        _build_user_confirm_event(
            storage,
            USER_ID,
            AGENT_ID,
            SESSION_ID,
            response,
        ),
    )
    assert event is None


def test_missing_session_returns_none() -> None:
    storage = _FakeStorage(None)
    event = asyncio.run(
        _build_user_confirm_event(
            storage,
            USER_ID,
            AGENT_ID,
            SESSION_ID,
            _confirm_response("confirm"),
        ),
    )
    assert event is None
