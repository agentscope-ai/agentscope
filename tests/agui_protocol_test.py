# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for AGUI protocol middleware."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.app.middleware import AGUIProtocolMiddleware
from agentscope.event import (
    ConfirmResult,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
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
from agentscope.message import ToolCallBlock, ToolResultBlock, ToolResultState


class AGUIProtocolLifecycleTest(IsolatedAsyncioTestCase):
    """Test lifecycle event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    async def test_reply_start_to_run_started(self) -> None:
        """Test ReplyStartEvent -> RUN_STARTED."""
        event = ReplyStartEvent(
            session_id="sess_1",
            reply_id="reply_1",
            name="agent",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "RUN_STARTED")
        self.assertEqual(result["threadId"], "sess_1")
        self.assertEqual(result["runId"], "reply_1")
        self.assertNotIn("name", result)

    async def test_reply_end_to_run_finished(self) -> None:
        """Test ReplyEndEvent -> RUN_FINISHED."""
        event = ReplyEndEvent(
            session_id="sess_1",
            reply_id="reply_1",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "RUN_FINISHED")
        self.assertEqual(result["threadId"], "sess_1")
        self.assertEqual(result["runId"], "reply_1")

    async def test_exceed_max_iters_to_run_error(self) -> None:
        """Test ExceedMaxItersEvent -> RUN_ERROR."""
        event = ExceedMaxItersEvent(
            reply_id="reply_1",
            name="my_agent",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "RUN_ERROR")
        self.assertIn("my_agent", result["message"])
        self.assertEqual(result["code"], "exceed_max_iters")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AGUIProtocolStepTest(IsolatedAsyncioTestCase):
    """Test model call -> step event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    async def test_model_call_start_to_step_started(self) -> None:
        """Test ModelCallStartEvent -> STEP_STARTED."""
        event = ModelCallStartEvent(
            reply_id="reply_1",
            model_name="gpt-4",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "STEP_STARTED")
        self.assertEqual(result["stepName"], "gpt-4")

    async def test_model_call_end_to_step_finished(self) -> None:
        """Test ModelCallEndEvent -> STEP_FINISHED with matching step_name."""
        start_event = ModelCallStartEvent(
            reply_id="reply_1",
            model_name="gpt-4",
        )
        self.mw._convert_to_protocol(start_event)

        end_event = ModelCallEndEvent(
            reply_id="reply_1",
            input_tokens=100,
            output_tokens=50,
        )
        result = self.mw._convert_to_protocol(end_event)

        self.assertEqual(result["type"], "STEP_FINISHED")
        self.assertEqual(result["stepName"], "gpt-4")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AGUIProtocolTextMessageTest(IsolatedAsyncioTestCase):
    """Test text block -> text message event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    async def test_text_block_start(self) -> None:
        """Test TextBlockStartEvent -> TEXT_MESSAGE_START."""
        event = TextBlockStartEvent(
            reply_id="reply_1",
            block_id="block_1",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "TEXT_MESSAGE_START")
        self.assertEqual(result["messageId"], "block_1")

    async def test_text_block_delta(self) -> None:
        """Test TextBlockDeltaEvent -> TEXT_MESSAGE_CONTENT."""
        event = TextBlockDeltaEvent(
            reply_id="reply_1",
            block_id="block_1",
            delta="Hello, ",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "TEXT_MESSAGE_CONTENT")
        self.assertEqual(result["messageId"], "block_1")
        self.assertEqual(result["delta"], "Hello, ")

    async def test_text_block_end(self) -> None:
        """Test TextBlockEndEvent -> TEXT_MESSAGE_END."""
        event = TextBlockEndEvent(
            reply_id="reply_1",
            block_id="block_1",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "TEXT_MESSAGE_END")
        self.assertEqual(result["messageId"], "block_1")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AGUIProtocolReasoningTest(IsolatedAsyncioTestCase):
    """Test thinking block -> reasoning message event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    async def test_thinking_block_start(self) -> None:
        """Test ThinkingBlockStartEvent -> REASONING_MESSAGE_START."""
        event = ThinkingBlockStartEvent(
            reply_id="reply_1",
            block_id="think_1",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "REASONING_MESSAGE_START")
        self.assertEqual(result["messageId"], "think_1")
        self.assertEqual(result["role"], "reasoning")

    async def test_thinking_block_delta(self) -> None:
        """Test ThinkingBlockDeltaEvent -> REASONING_MESSAGE_CONTENT."""
        event = ThinkingBlockDeltaEvent(
            reply_id="reply_1",
            block_id="think_1",
            delta="Let me think...",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "REASONING_MESSAGE_CONTENT")
        self.assertEqual(result["messageId"], "think_1")
        self.assertEqual(result["delta"], "Let me think...")

    async def test_thinking_block_end(self) -> None:
        """Test ThinkingBlockEndEvent -> REASONING_MESSAGE_END."""
        event = ThinkingBlockEndEvent(
            reply_id="reply_1",
            block_id="think_1",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "REASONING_MESSAGE_END")
        self.assertEqual(result["messageId"], "think_1")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AGUIProtocolToolCallTest(IsolatedAsyncioTestCase):
    """Test tool call event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    async def test_tool_call_start(self) -> None:
        """Test ToolCallStartEvent -> TOOL_CALL_START."""
        event = ToolCallStartEvent(
            reply_id="reply_1",
            tool_call_id="tc_1",
            tool_call_name="search",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "TOOL_CALL_START")
        self.assertEqual(result["toolCallId"], "tc_1")
        self.assertEqual(result["toolCallName"], "search")
        self.assertEqual(result["parentMessageId"], "reply_1")

    async def test_tool_call_delta(self) -> None:
        """Test ToolCallDeltaEvent -> TOOL_CALL_ARGS."""
        event = ToolCallDeltaEvent(
            reply_id="reply_1",
            tool_call_id="tc_1",
            delta='{"query": "hello"}',
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "TOOL_CALL_ARGS")
        self.assertEqual(result["toolCallId"], "tc_1")
        self.assertEqual(result["delta"], '{"query": "hello"}')

    async def test_tool_call_end(self) -> None:
        """Test ToolCallEndEvent -> TOOL_CALL_END."""
        event = ToolCallEndEvent(
            reply_id="reply_1",
            tool_call_id="tc_1",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "TOOL_CALL_END")
        self.assertEqual(result["toolCallId"], "tc_1")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AGUIProtocolToolResultTest(IsolatedAsyncioTestCase):
    """Test tool result event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    async def test_tool_result_end_with_buffered_content(self) -> None:
        """Test that ToolResultEndEvent carries accumulated text content."""
        self.mw._convert_to_protocol(
            ToolResultTextDeltaEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                delta="partial ",
            ),
        )
        self.mw._convert_to_protocol(
            ToolResultTextDeltaEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                delta="result",
            ),
        )

        result = self.mw._convert_to_protocol(
            ToolResultEndEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                state=ToolResultState.SUCCESS,
            ),
        )

        self.assertEqual(result["type"], "TOOL_CALL_RESULT")
        self.assertEqual(result["toolCallId"], "tc_1")
        self.assertEqual(result["messageId"], "reply_1")
        self.assertEqual(result["content"], "partial result")

    async def test_tool_result_end_fallback_to_state(self) -> None:
        """Test that ToolResultEndEvent falls back to state when no buffer."""
        result = self.mw._convert_to_protocol(
            ToolResultEndEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                state=ToolResultState.ERROR,
            ),
        )

        self.assertEqual(result["type"], "TOOL_CALL_RESULT")
        self.assertEqual(result["content"], "error")

    async def test_tool_result_start_to_custom(self) -> None:
        """Test ToolResultStartEvent -> CUSTOM."""
        event = ToolResultStartEvent(
            reply_id="reply_1",
            tool_call_id="tc_1",
            tool_call_name="search",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "tool_result_start")
        self.assertIsInstance(result["value"], dict)

    async def test_tool_result_text_delta_to_custom(self) -> None:
        """Test ToolResultTextDeltaEvent -> CUSTOM."""
        event = ToolResultTextDeltaEvent(
            reply_id="reply_1",
            tool_call_id="tc_1",
            delta="partial result",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "tool_result_text_delta")
        self.assertEqual(result["value"]["delta"], "partial result")

    async def test_tool_result_data_delta_to_custom(self) -> None:
        """Test ToolResultDataDeltaEvent -> CUSTOM."""
        event = ToolResultDataDeltaEvent(
            reply_id="reply_1",
            tool_call_id="tc_1",
            media_type="image/png",
            data="base64data",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "tool_result_data_delta")
        self.assertEqual(result["value"]["media_type"], "image/png")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AGUIProtocolDataBlockTest(IsolatedAsyncioTestCase):
    """Test data block -> custom event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    async def test_data_block_start(self) -> None:
        """Test DataBlockStartEvent -> CUSTOM."""
        event = DataBlockStartEvent(
            reply_id="reply_1",
            block_id="db_1",
            media_type="image/png",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "data_block_start")
        self.assertEqual(result["value"]["media_type"], "image/png")

    async def test_data_block_delta(self) -> None:
        """Test DataBlockDeltaEvent -> CUSTOM."""
        event = DataBlockDeltaEvent(
            reply_id="reply_1",
            block_id="db_1",
            data="base64chunk",
            media_type="image/png",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "data_block_delta")
        self.assertEqual(result["value"]["data"], "base64chunk")

    async def test_data_block_end(self) -> None:
        """Test DataBlockEndEvent -> CUSTOM."""
        event = DataBlockEndEvent(
            reply_id="reply_1",
            block_id="db_1",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "data_block_end")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AGUIProtocolPermissionTest(IsolatedAsyncioTestCase):
    """Test permission-related event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    async def test_require_user_confirm(self) -> None:
        """Test RequireUserConfirmEvent -> CUSTOM."""
        event = RequireUserConfirmEvent(
            reply_id="reply_1",
            tool_calls=[
                ToolCallBlock(
                    id="tc_1",
                    name="bash",
                    input='{"command": "ls"}',
                ),
            ],
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "require_user_confirm")
        self.assertIn("tool_calls", result["value"])

    async def test_require_external_execution(self) -> None:
        """Test RequireExternalExecutionEvent -> CUSTOM."""
        event = RequireExternalExecutionEvent(
            reply_id="reply_1",
            tool_calls=[
                ToolCallBlock(
                    id="tc_2",
                    name="external_api",
                    input="{}",
                ),
            ],
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "require_external_execution")

    async def test_user_confirm_result(self) -> None:
        """Test UserConfirmResultEvent -> CUSTOM."""
        event = UserConfirmResultEvent(
            reply_id="reply_1",
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id="tc_1",
                        name="bash",
                        input='{"command": "ls"}',
                    ),
                ),
            ],
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "user_confirm_result")

    async def test_external_execution_result(self) -> None:
        """Test ExternalExecutionResultEvent -> CUSTOM."""
        event = ExternalExecutionResultEvent(
            reply_id="reply_1",
            execution_results=[
                ToolResultBlock(
                    id="tc_2",
                    name="external_api",
                    output="result data",
                ),
            ],
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "CUSTOM")
        self.assertEqual(result["name"], "external_execution_result")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AGUIProtocolCamelCaseTest(IsolatedAsyncioTestCase):
    """Verify that all output dicts use camelCase keys as AGUI requires."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AGUIProtocolMiddleware(app=MagicMock())

    def _assert_no_snake_case_keys(self, d: dict, context: str) -> None:
        """Assert none of the top-level keys contain underscores."""
        for key in d:
            if key == "value":
                continue
            self.assertNotIn(
                "_",
                key,
                f"Key '{key}' in {context} should be camelCase",
            )

    async def test_all_standard_events_produce_camel_case(self) -> None:
        """Test that all directly-mapped events produce camelCase keys."""
        events = [
            ReplyStartEvent(
                session_id="s",
                reply_id="r",
                name="a",
            ),
            ReplyEndEvent(session_id="s", reply_id="r"),
            ModelCallStartEvent(reply_id="r", model_name="m"),
            ModelCallEndEvent(
                reply_id="r",
                input_tokens=1,
                output_tokens=1,
            ),
            TextBlockStartEvent(reply_id="r", block_id="b"),
            TextBlockDeltaEvent(reply_id="r", block_id="b", delta="x"),
            TextBlockEndEvent(reply_id="r", block_id="b"),
            ThinkingBlockStartEvent(reply_id="r", block_id="b"),
            ThinkingBlockDeltaEvent(reply_id="r", block_id="b", delta="x"),
            ThinkingBlockEndEvent(reply_id="r", block_id="b"),
            ToolCallStartEvent(
                reply_id="r",
                tool_call_id="t",
                tool_call_name="n",
            ),
            ToolCallDeltaEvent(
                reply_id="r",
                tool_call_id="t",
                delta="x",
            ),
            ToolCallEndEvent(reply_id="r", tool_call_id="t"),
            ExceedMaxItersEvent(reply_id="r", name="a"),
        ]

        for event in events:
            result = self.mw._convert_to_protocol(event)
            self._assert_no_snake_case_keys(
                result,
                type(event).__name__,
            )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
