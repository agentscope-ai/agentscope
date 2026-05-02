# -*- coding: utf-8 -*-
"""Event test."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.event import (
    ConfirmResult,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    EventType,
    ExceedMaxItersEvent,
    ExternalExecutionResultEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)
from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.permission import PermissionBehavior, PermissionRule


class EventTest(IsolatedAsyncioTestCase):
    """The event test case."""

    def _assert_event_round_trip(
        self,
        event_cls: type,
        payload: dict[str, Any],
        expected: dict[str, Any],
    ) -> None:
        """Assert event creation, serialization, and deserialization."""
        event = event_cls(**payload)
        event_dump = event.model_dump(mode="json")

        self.assertDictEqual(
            event_dump,
            {
                "id": AnyString(),
                "created_at": AnyString(),
                **expected,
            },
        )

        validated_event = event_cls.model_validate(event_dump)
        self.assertDictEqual(
            validated_event.model_dump(mode="json"),
            event_dump,
        )

    async def test_event_type_values(self) -> None:
        """Test event type enum values."""
        self.assertEqual(EventType.REPLY_START.value, "REPLY_START")
        self.assertEqual(EventType.MODEL_CALL_END.value, "MODEL_CALL_END")
        self.assertEqual(
            EventType.EXTERNAL_EXECUTION_RESULT.value,
            "EXTERNAL_EXECUTION_RESULT",
        )

    async def test_reply_events(self) -> None:
        """Test reply event creation and serialization."""
        self._assert_event_round_trip(
            ReplyStartEvent,
            {
                "session_id": "test_session",
                "reply_id": "test_reply",
                "name": "Friday",
            },
            {
                "type": "REPLY_START",
                "session_id": "test_session",
                "reply_id": "test_reply",
                "name": "Friday",
                "role": "assistant",
            },
        )
        self._assert_event_round_trip(
            ReplyEndEvent,
            {
                "session_id": "test_session",
                "reply_id": "test_reply",
            },
            {
                "type": "REPLY_END",
                "session_id": "test_session",
                "reply_id": "test_reply",
            },
        )

    async def test_model_call_events(self) -> None:
        """Test model call event creation and serialization."""
        self._assert_event_round_trip(
            ModelCallStartEvent,
            {
                "reply_id": "test_reply",
                "model_name": "mock-model",
            },
            {
                "type": "MODEL_CALL_START",
                "reply_id": "test_reply",
                "model_name": "mock-model",
            },
        )
        self._assert_event_round_trip(
            ModelCallEndEvent,
            {
                "reply_id": "test_reply",
                "input_tokens": 11,
                "output_tokens": 22,
            },
            {
                "type": "MODEL_CALL_END",
                "reply_id": "test_reply",
                "input_tokens": 11,
                "output_tokens": 22,
            },
        )

    async def test_content_block_events(self) -> None:
        """Test text, data, and thinking block events."""
        cases: list[tuple[type, dict[str, Any], dict[str, Any]]] = [
            (
                TextBlockStartEvent,
                {"reply_id": "test_reply", "block_id": "text_block"},
                {
                    "type": "TEXT_BLOCK_START",
                    "reply_id": "test_reply",
                    "block_id": "text_block",
                },
            ),
            (
                TextBlockDeltaEvent,
                {
                    "reply_id": "test_reply",
                    "block_id": "text_block",
                    "delta": "hello",
                },
                {
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": "test_reply",
                    "block_id": "text_block",
                    "delta": "hello",
                },
            ),
            (
                TextBlockEndEvent,
                {"reply_id": "test_reply", "block_id": "text_block"},
                {
                    "type": "TEXT_BLOCK_END",
                    "reply_id": "test_reply",
                    "block_id": "text_block",
                },
            ),
            (
                DataBlockStartEvent,
                {
                    "reply_id": "test_reply",
                    "block_id": "data_block",
                    "media_type": "image/png",
                },
                {
                    "type": "DATA_BLOCK_START",
                    "reply_id": "test_reply",
                    "block_id": "data_block",
                    "media_type": "image/png",
                },
            ),
            (
                DataBlockDeltaEvent,
                {
                    "reply_id": "test_reply",
                    "block_id": "data_block",
                    "data": "abc",
                    "media_type": "image/png",
                },
                {
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "test_reply",
                    "block_id": "data_block",
                    "data": "abc",
                    "media_type": "image/png",
                },
            ),
            (
                DataBlockEndEvent,
                {"reply_id": "test_reply", "block_id": "data_block"},
                {
                    "type": "DATA_BLOCK_END",
                    "reply_id": "test_reply",
                    "block_id": "data_block",
                },
            ),
            (
                ThinkingBlockStartEvent,
                {"reply_id": "test_reply", "block_id": "thinking_block"},
                {
                    "type": "THINKING_BLOCK_START",
                    "reply_id": "test_reply",
                    "block_id": "thinking_block",
                },
            ),
            (
                ThinkingBlockDeltaEvent,
                {
                    "reply_id": "test_reply",
                    "block_id": "thinking_block",
                    "delta": "thinking",
                },
                {
                    "type": "THINKING_BLOCK_DELTA",
                    "reply_id": "test_reply",
                    "block_id": "thinking_block",
                    "delta": "thinking",
                },
            ),
            (
                ThinkingBlockEndEvent,
                {"reply_id": "test_reply", "block_id": "thinking_block"},
                {
                    "type": "THINKING_BLOCK_END",
                    "reply_id": "test_reply",
                    "block_id": "thinking_block",
                },
            ),
        ]

        for event_cls, payload, expected in cases:
            with self.subTest(event_cls=event_cls.__name__):
                self._assert_event_round_trip(event_cls, payload, expected)

    async def test_tool_call_and_result_events(self) -> None:
        """Test tool call and tool result events."""
        cases: list[tuple[type, dict[str, Any], dict[str, Any]]] = [
            (
                ToolCallStartEvent,
                {
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "tool_call_name": "search",
                },
                {
                    "type": "TOOL_CALL_START",
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "tool_call_name": "search",
                },
            ),
            (
                ToolCallDeltaEvent,
                {
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "delta": '{"query": "AgentScope"}',
                },
                {
                    "type": "TOOL_CALL_DELTA",
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "delta": '{"query": "AgentScope"}',
                },
            ),
            (
                ToolCallEndEvent,
                {"reply_id": "test_reply", "tool_call_id": "call_1"},
                {
                    "type": "TOOL_CALL_END",
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                },
            ),
            (
                ToolResultStartEvent,
                {
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "tool_call_name": "search",
                },
                {
                    "type": "TOOL_RESULT_START",
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "tool_call_name": "search",
                },
            ),
            (
                ToolResultTextDeltaEvent,
                {
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "delta": "result",
                },
                {
                    "type": "TOOL_RESULT_TEXT_DELTA",
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "delta": "result",
                },
            ),
            (
                ToolResultDataDeltaEvent,
                {
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "media_type": "image/png",
                    "data": "abc",
                },
                {
                    "type": "TOOL_RESULT_DATA_DELTA",
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "media_type": "image/png",
                    "data": "abc",
                    "url": None,
                },
            ),
            (
                ToolResultEndEvent,
                {
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "state": "success",
                },
                {
                    "type": "TOOL_RESULT_END",
                    "reply_id": "test_reply",
                    "tool_call_id": "call_1",
                    "state": "success",
                },
            ),
        ]

        for event_cls, payload, expected in cases:
            with self.subTest(event_cls=event_cls.__name__):
                self._assert_event_round_trip(event_cls, payload, expected)

    async def test_control_events(self) -> None:
        """Test control event creation and serialization."""
        tool_call = ToolCallBlock(
            id="call_1",
            name="search",
            input='{"query": "AgentScope"}',
        )
        self._assert_event_round_trip(
            ExceedMaxItersEvent,
            {"reply_id": "test_reply", "name": "Friday"},
            {
                "type": "EXCEED_MAX_ITERS",
                "reply_id": "test_reply",
                "name": "Friday",
            },
        )
        self._assert_event_round_trip(
            RequireUserConfirmEvent,
            {"reply_id": "test_reply", "tool_calls": [tool_call]},
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": "test_reply",
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": "call_1",
                        "name": "search",
                        "input": '{"query": "AgentScope"}',
                        "state": "pending",
                    },
                ],
            },
        )
        self._assert_event_round_trip(
            RequireExternalExecutionEvent,
            {"reply_id": "test_reply", "tool_calls": [tool_call]},
            {
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": "test_reply",
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": "call_1",
                        "name": "search",
                        "input": '{"query": "AgentScope"}',
                        "state": "pending",
                    },
                ],
            },
        )

    async def test_user_confirm_result_event(self) -> None:
        """Test user confirmation result event with nested rules."""
        tool_call = ToolCallBlock(
            id="call_1",
            name="search",
            input='{"query": "AgentScope"}',
        )
        confirm_result = ConfirmResult(
            confirmed=True,
            tool_call=tool_call,
            rules=[
                PermissionRule(
                    tool_name="search",
                    rule_content="AgentScope",
                    behavior=PermissionBehavior.ALLOW,
                    source="userSettings",
                ),
            ],
        )

        self._assert_event_round_trip(
            UserConfirmResultEvent,
            {
                "reply_id": "test_reply",
                "confirm_results": [confirm_result],
            },
            {
                "type": "USER_CONFIRM_RESULT",
                "reply_id": "test_reply",
                "confirm_results": [
                    {
                        "confirmed": True,
                        "tool_call": {
                            "type": "tool_call",
                            "id": "call_1",
                            "name": "search",
                            "input": '{"query": "AgentScope"}',
                            "state": "pending",
                        },
                        "rules": [
                            {
                                "tool_name": "search",
                                "rule_content": "AgentScope",
                                "behavior": "allow",
                                "source": "userSettings",
                            },
                        ],
                    },
                ],
            },
        )

    async def test_external_execution_result_event(self) -> None:
        """Test external execution result event with nested tool results."""
        execution_result = ToolResultBlock(
            id="call_1",
            name="search",
            output=[TextBlock(id="text_1", text="result")],
            state="success",
        )

        self._assert_event_round_trip(
            ExternalExecutionResultEvent,
            {
                "reply_id": "test_reply",
                "execution_results": [execution_result],
            },
            {
                "type": "EXTERNAL_EXECUTION_RESULT",
                "reply_id": "test_reply",
                "execution_results": [
                    {
                        "type": "tool_result",
                        "id": "call_1",
                        "name": "search",
                        "output": [
                            {
                                "type": "text",
                                "text": "result",
                                "id": "text_1",
                            },
                        ],
                        "state": "success",
                    },
                ],
            },
        )
