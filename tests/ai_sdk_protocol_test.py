# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for the AI SDK UI message stream protocol middleware."""

import json
from typing import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from agentscope.app.middleware import AISDKProtocolMiddleware
from agentscope.app.middleware import UI_MESSAGE_STREAM_HEADER
from agentscope.event import (
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyFinishedReason,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import ToolResultState
from agentscope.model import FinishedReason


async def _collect_stream(
    mw: AISDKProtocolMiddleware,
    chunks: list[str],
) -> str:
    """Collect converted stream chunks as text."""

    async def _stream() -> AsyncGenerator[str, None]:
        """Yield the provided chunks."""
        for chunk in chunks:
            yield chunk

    out: list[str] = []
    async for item in mw._convert_stream(_stream()):
        out.append(item.decode("utf-8"))
    return "".join(out)


class AISDKProtocolStreamTest(IsolatedAsyncioTestCase):
    """Test stream-level conversion behavior."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AISDKProtocolMiddleware(app=MagicMock())

    async def test_raw_json_stream_is_converted(self) -> None:
        """Test raw AgentEvent JSON stream conversion."""
        event = ReplyStartEvent(
            session_id="sess_1",
            reply_id="reply_1",
            name="agent",
        )

        body = await _collect_stream(self.mw, [event.model_dump_json()])
        data = json.loads(body)

        self.assertEqual(data["type"], "start")
        self.assertEqual(data["messageId"], "reply_1")

    async def test_sse_data_frame_is_converted(self) -> None:
        """Test AgentEvent JSON inside an SSE data frame is converted."""
        event = ReplyStartEvent(
            session_id="sess_1",
            reply_id="reply_1",
            name="agent",
        )

        body = await _collect_stream(
            self.mw,
            [f"data: {event.model_dump_json()}\n\n"],
        )

        self.assertTrue(body.startswith("data: "))
        data = json.loads(body.removeprefix("data: ").strip())
        self.assertEqual(data["type"], "start")
        # the AgentScope field name must not leak into the protocol frame
        self.assertNotIn("session_id", data)

    async def test_sse_heartbeat_is_passed_through(self) -> None:
        """Test SSE heartbeat frames are not modified."""
        self.assertEqual(
            await _collect_stream(self.mw, [":\n\n"]),
            ":\n\n",
        )

    async def test_fastapi_sse_response_carries_protocol_header(self) -> None:
        """Test the middleware converts a real SSE response and labels it."""
        app = FastAPI()
        app.add_middleware(AISDKProtocolMiddleware)

        @app.get("/sessions/sess_1/stream")
        async def stream() -> StreamingResponse:
            events = [
                ReplyStartEvent(
                    session_id="sess_1",
                    reply_id="reply_1",
                    name="agent",
                ),
                TextBlockStartEvent(reply_id="reply_1", block_id="block_1"),
                TextBlockDeltaEvent(
                    reply_id="reply_1",
                    block_id="block_1",
                    delta="你好",
                ),
                TextBlockEndEvent(reply_id="reply_1", block_id="block_1"),
                ReplyEndEvent(
                    session_id="sess_1",
                    reply_id="reply_1",
                    finished_reason=ReplyFinishedReason.COMPLETED,
                ),
            ]

            async def gen() -> AsyncGenerator[str, None]:
                for event in events:
                    yield f"data: {event.model_dump_json()}\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        client = TestClient(app)
        with client.stream("GET", "/sessions/sess_1/stream") as response:
            self.assertEqual(
                response.headers.get(UI_MESSAGE_STREAM_HEADER),
                "1",
            )
            body = "".join(response.iter_text())

        frames = [
            json.loads(line.removeprefix("data: ").strip())
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertListEqual(
            [frame["type"] for frame in frames],
            ["start", "text-start", "text-delta", "text-end", "finish"],
        )
        self.assertEqual(frames[2]["delta"], "你好")

    async def test_fastapi_json_response_is_not_converted(self) -> None:
        """Test non-SSE responses are outside protocol conversion scope."""
        app = FastAPI()
        app.add_middleware(AISDKProtocolMiddleware)

        @app.get("/event")
        def event() -> JSONResponse:
            agent_event = ReplyStartEvent(
                session_id="sess_1",
                reply_id="reply_1",
                name="agent",
            )
            return JSONResponse(agent_event.model_dump(mode="json"))

        client = TestClient(app)
        response = client.get("/event")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(UI_MESSAGE_STREAM_HEADER, response.headers)
        data = response.json()
        self.assertEqual(data["type"], "REPLY_START")
        self.assertEqual(data["session_id"], "sess_1")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AISDKProtocolLifecycleTest(IsolatedAsyncioTestCase):
    """Test lifecycle event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AISDKProtocolMiddleware(app=MagicMock())

    async def test_reply_start_to_start(self) -> None:
        """Test ReplyStartEvent -> start."""
        event = ReplyStartEvent(
            session_id="sess_1",
            reply_id="reply_1",
            name="agent",
        )
        result = self.mw._convert_to_protocol(event)

        self.assertDictEqual(
            result,
            {"type": "start", "messageId": "reply_1"},
        )

    async def test_reply_end_to_finish(self) -> None:
        """Test ReplyEndEvent -> finish."""
        event = ReplyEndEvent(
            session_id="sess_1",
            reply_id="reply_1",
            finished_reason=ReplyFinishedReason.COMPLETED,
        )
        result = self.mw._convert_to_protocol(event)

        self.assertDictEqual(result, {"type": "finish"})

    async def test_exceed_max_iters_to_error(self) -> None:
        """Test ReplyEndEvent with EXCEED_MAX_ITERS -> error."""
        event = ReplyEndEvent(
            session_id="sess_1",
            reply_id="reply_1",
            finished_reason=ReplyFinishedReason.EXCEED_MAX_ITERS,
        )
        result = self.mw._convert_to_protocol(event)

        self.assertEqual(result["type"], "error")
        self.assertIn("maximum", result["errorText"])

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AISDKProtocolTextTest(IsolatedAsyncioTestCase):
    """Test text block -> text chunk conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AISDKProtocolMiddleware(app=MagicMock())

    async def test_text_block_lifecycle(self) -> None:
        """Test TextBlock* -> text-start/delta/end."""
        start = self.mw._convert_to_protocol(
            TextBlockStartEvent(reply_id="reply_1", block_id="block_1"),
        )
        delta = self.mw._convert_to_protocol(
            TextBlockDeltaEvent(
                reply_id="reply_1",
                block_id="block_1",
                delta="Hello, ",
            ),
        )
        end = self.mw._convert_to_protocol(
            TextBlockEndEvent(reply_id="reply_1", block_id="block_1"),
        )

        self.assertDictEqual(
            start,
            {"type": "text-start", "id": "block_1"},
        )
        self.assertDictEqual(
            delta,
            {"type": "text-delta", "id": "block_1", "delta": "Hello, "},
        )
        self.assertDictEqual(end, {"type": "text-end", "id": "block_1"})

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AISDKProtocolReasoningTest(IsolatedAsyncioTestCase):
    """Test thinking block -> reasoning chunk conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AISDKProtocolMiddleware(app=MagicMock())

    async def test_thinking_block_lifecycle(self) -> None:
        """Test ThinkingBlock* -> reasoning-start/delta/end."""
        start = self.mw._convert_to_protocol(
            ThinkingBlockStartEvent(reply_id="reply_1", block_id="think_1"),
        )
        delta = self.mw._convert_to_protocol(
            ThinkingBlockDeltaEvent(
                reply_id="reply_1",
                block_id="think_1",
                delta="Let me think...",
            ),
        )
        end = self.mw._convert_to_protocol(
            ThinkingBlockEndEvent(reply_id="reply_1", block_id="think_1"),
        )

        self.assertDictEqual(
            start,
            {"type": "reasoning-start", "id": "think_1"},
        )
        self.assertDictEqual(
            delta,
            {
                "type": "reasoning-delta",
                "id": "think_1",
                "delta": "Let me think...",
            },
        )
        self.assertDictEqual(
            end,
            {"type": "reasoning-end", "id": "think_1"},
        )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AISDKProtocolToolCallTest(IsolatedAsyncioTestCase):
    """Test tool call event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AISDKProtocolMiddleware(app=MagicMock())

    async def test_tool_call_start(self) -> None:
        """Test ToolCallStartEvent -> tool-input-start."""
        result = self.mw._convert_to_protocol(
            ToolCallStartEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                tool_call_name="search",
            ),
        )

        self.assertDictEqual(
            result,
            {
                "type": "tool-input-start",
                "toolCallId": "tc_1",
                "toolName": "search",
            },
        )

    async def test_tool_call_delta(self) -> None:
        """Test ToolCallDeltaEvent -> tool-input-delta."""
        result = self.mw._convert_to_protocol(
            ToolCallDeltaEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                delta='{"query":',
            ),
        )

        self.assertDictEqual(
            result,
            {
                "type": "tool-input-delta",
                "toolCallId": "tc_1",
                "inputTextDelta": '{"query":',
            },
        )

    async def test_fragments_are_assembled_into_parsed_input(self) -> None:
        """Test ToolCallEndEvent emits the parsed object, not the fragments."""
        self.mw._convert_to_protocol(
            ToolCallStartEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                tool_call_name="search",
            ),
        )
        for fragment in ('{"query":', ' "hello"}'):
            self.mw._convert_to_protocol(
                ToolCallDeltaEvent(
                    reply_id="reply_1",
                    tool_call_id="tc_1",
                    delta=fragment,
                ),
            )

        result = self.mw._convert_to_protocol(
            ToolCallEndEvent(reply_id="reply_1", tool_call_id="tc_1"),
        )

        self.assertDictEqual(
            result,
            {
                "type": "tool-input-available",
                "toolCallId": "tc_1",
                "toolName": "search",
                "input": {"query": "hello"},
            },
        )
        # buffered fragments must not leak into the next tool call
        self.assertDictEqual(self.mw._tool_inputs, {})
        self.assertDictEqual(self.mw._tool_names, {})

    async def test_invalid_json_fragments_emit_error_not_bad_input(
        self,
    ) -> None:
        """Test unparseable arguments produce an error chunk."""
        result = self.mw._convert_to_protocol(
            ToolCallEndEvent(reply_id="reply_1", tool_call_id="tc_1"),
        )
        self.mw._convert_to_protocol(
            ToolCallStartEvent(
                reply_id="reply_1",
                tool_call_id="tc_2",
                tool_call_name="search",
            ),
        )
        self.mw._convert_to_protocol(
            ToolCallDeltaEvent(
                reply_id="reply_1",
                tool_call_id="tc_2",
                delta='{"query": "unterminated',
            ),
        )
        broken = self.mw._convert_to_protocol(
            ToolCallEndEvent(reply_id="reply_1", tool_call_id="tc_2"),
        )

        self.assertEqual(result["type"], "tool-input-available")
        self.assertDictEqual(result["input"], {})
        self.assertEqual(broken["type"], "error")
        self.assertIn("not valid JSON", broken["errorText"])

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AISDKProtocolToolResultTest(IsolatedAsyncioTestCase):
    """Test tool result event conversions."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AISDKProtocolMiddleware(app=MagicMock())

    async def test_result_end_carries_accumulated_output(self) -> None:
        """Test ToolResultEndEvent folds the streamed output."""
        self.mw._convert_to_protocol(
            ToolResultStartEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                tool_call_name="search",
            ),
        )
        for delta in ("partial ", "output"):
            self.mw._convert_to_protocol(
                ToolResultTextDeltaEvent(
                    reply_id="reply_1",
                    tool_call_id="tc_1",
                    delta=delta,
                ),
            )

        result = self.mw._convert_to_protocol(
            ToolResultEndEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                state=ToolResultState.SUCCESS,
            ),
        )

        self.assertDictEqual(
            result,
            {
                "type": "tool-output-available",
                "toolCallId": "tc_1",
                "output": "partial output",
            },
        )
        self.assertDictEqual(self.mw._tool_outputs, {})

    async def test_result_end_falls_back_to_state(self) -> None:
        """Test ToolResultEndEvent falls back to the state when empty."""
        result = self.mw._convert_to_protocol(
            ToolResultEndEvent(
                reply_id="reply_1",
                tool_call_id="tc_1",
                state=ToolResultState.ERROR,
            ),
        )

        self.assertEqual(result["type"], "tool-output-available")
        self.assertEqual(result["output"], "error")

    async def test_concurrent_tool_results_stay_separate(self) -> None:
        """Test two tool results of one reply keep their own output."""
        for tool_call_id, delta in (("tc_1", "A"), ("tc_2", "B")):
            self.mw._convert_to_protocol(
                ToolResultStartEvent(
                    reply_id="reply_1",
                    tool_call_id=tool_call_id,
                    tool_call_name="search",
                ),
            )
            self.mw._convert_to_protocol(
                ToolResultTextDeltaEvent(
                    reply_id="reply_1",
                    tool_call_id=tool_call_id,
                    delta=delta,
                ),
            )

        results = [
            self.mw._convert_to_protocol(
                ToolResultEndEvent(
                    reply_id="reply_1",
                    tool_call_id=tool_call_id,
                    state=ToolResultState.SUCCESS,
                ),
            )
            for tool_call_id in ("tc_2", "tc_1")
        ]

        self.assertListEqual(
            [result["output"] for result in results],
            ["B", "A"],
        )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class AISDKProtocolCustomDataPartTest(IsolatedAsyncioTestCase):
    """Test events without a chunk equivalent become data parts."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mw = AISDKProtocolMiddleware(app=MagicMock())

    async def test_model_call_events_to_data_parts(self) -> None:
        """Test model call events are forwarded as custom data parts."""
        start = self.mw._convert_to_protocol(
            ModelCallStartEvent(reply_id="reply_1", model_name="gpt-4"),
        )
        end = self.mw._convert_to_protocol(
            ModelCallEndEvent(
                reply_id="reply_1",
                input_tokens=10,
                output_tokens=5,
                finished_reason=FinishedReason.COMPLETED,
            ),
        )

        self.assertEqual(start["type"], "data-agentscope")
        self.assertEqual(start["data"]["event"], "model_call_start")
        self.assertEqual(start["data"]["model_name"], "gpt-4")
        self.assertEqual(end["type"], "data-agentscope")
        self.assertEqual(end["data"]["event"], "model_call_end")

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
