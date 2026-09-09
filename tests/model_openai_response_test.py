# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAIResponseModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
OpenAI Responses API uses event-based streaming with response.completed.
"""
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel

from utils import AnyString

from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    ThinkingBlock,
)
from agentscope.model import OpenAIResponseModel
from agentscope.credential import OpenAICredential
from agentscope.tool import ToolChoice

A = AnyString()


class _MockReasoningSummary(BaseModel):
    """Mock an OpenAI reasoning summary item."""

    text: str
    type: str = "summary_text"


class _MockReasoningItem(BaseModel):
    """Mock an OpenAI reasoning output item."""

    id: str
    summary: list[_MockReasoningSummary]
    type: str = "reasoning"
    content: list[dict[str, Any]] | None = None
    encrypted_content: str | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return OpenAIResponseModel(
        credential=OpenAICredential(api_key="test"),
        model="o4-mini",
        stream=stream,
        context_size=200_000,
    )


def _mock_completion(
    text: Any = None,
    function_calls: Any = None,
    reasoning_summary: Any = None,
    reasoning_id: str = "rs_test123",
    response_id: str = "resp-openai-1",
    reasoning_output_item: Any = None,
) -> MagicMock:
    """Build a mock non-streaming Responses API response."""
    output = []

    if reasoning_output_item is not None:
        output.append(reasoning_output_item)

    elif reasoning_summary is not None:
        summary_texts = (
            reasoning_summary
            if isinstance(reasoning_summary, list)
            else [reasoning_summary]
            if reasoning_summary is not None
            else []
        )
        reasoning_item = _MockReasoningItem(
            id=reasoning_id,
            summary=[
                _MockReasoningSummary(text=summary_text)
                for summary_text in summary_texts
            ],
        )
        output.append(reasoning_item)

    if text:
        msg_item = MagicMock()
        msg_item.type = "message"
        part = MagicMock()
        part.type = "output_text"
        part.text = text
        msg_item.content = [part]
        output.append(msg_item)

    if function_calls:
        for fc in function_calls:
            fc_item = MagicMock()
            fc_item.type = "function_call"
            fc_item.id = fc["id"]
            fc_item.call_id = fc["call_id"]
            fc_item.name = fc["name"]
            fc_item.arguments = fc["arguments"]
            output.append(fc_item)

    resp = MagicMock()
    resp.id = response_id
    resp.output = output
    resp.usage = MagicMock()
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    resp.usage.input_tokens_details = None
    return resp


def _make_event(event_type: str, **kwargs: Any) -> MagicMock:
    """Build a mock Responses API streaming event."""
    event = MagicMock()
    event.type = event_type
    for key, val in kwargs.items():
        setattr(event, key, val)
    # Default: no response attribute
    if "response" not in kwargs:
        event.response = None
    return event


class _MockAsyncEventStream:
    """Mock async iterator over Response events."""

    def __init__(self, events: list) -> None:
        self._events = events
        self._index = 0
        self.exited = False

    async def __aenter__(self) -> "_MockAsyncEventStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.exited = True

    def __aiter__(self) -> "_MockAsyncEventStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event


# ---------------------------------------------------------------------------
# Non-streaming tests
# ---------------------------------------------------------------------------


class TestOpenAIResponseNonStream(IsolatedAsyncioTestCase):
    """Tests for OpenAIResponseModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_text_response(self) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        self.mock_client.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [TextBlock.model_construct(id=A, created_at=A, text="Hello!")],
            ),
        )
        self.assertEqual(result.id, "resp-openai-1")

    async def test_tool_call_response(
        self,
    ) -> None:
        """Parsing a tool-call response stores call_id as ToolCallBlock.id."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                function_calls=[
                    {
                        "id": "fc_abc",
                        "call_id": "call-1",
                        "name": "get_weather",
                        "arguments": '{"city":"BJ"}',
                    },
                ],
            ),
        )
        self.mock_client.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock.model_construct(
                        id="call-1",
                        created_at=A,
                        name="get_weather",
                        input='{"city":"BJ"}',
                    ),
                ],
            ),
        )

    async def test_native_multimodal_tool_result_request(self) -> None:
        """The model sends native multimodal function-call output."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="verified"),
        )
        self.mock_client.responses.create = mock_create
        messages = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call-image",
                        name="inspect_image",
                        input="{}",
                    ),
                    ToolResultBlock(
                        id="call-image",
                        name="inspect_image",
                        output=[
                            TextBlock(text="marker-before"),
                            DataBlock(
                                source=Base64Source(
                                    data="aW1hZ2U=",
                                    media_type="image/png",
                                ),
                            ),
                            TextBlock(text="marker-after"),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            ),
        ]

        await self.model(messages)

        self.assertListEqual(
            mock_create.await_args.kwargs["input"],
            [
                {
                    "type": "function_call",
                    "call_id": "call-image",
                    "name": "inspect_image",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-image",
                    "output": [
                        {"type": "input_text", "text": "marker-before"},
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,aW1hZ2U=",
                        },
                        {"type": "input_text", "text": "marker-after"},
                    ],
                },
            ],
        )

    async def test_reasoning_response(
        self,
    ) -> None:
        """Non-stream reasoning summary plus text returns both block types."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                reasoning_summary="Thinking step...",
                text="Answer",
                reasoning_id="rs_abc999",
            ),
        )
        self.mock_client.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        created_at=A,
                        thinking="Thinking step...",
                        reasoning_item_id="rs_abc999",
                        reasoning_item_raw={
                            "id": "rs_abc999",
                            "summary": [
                                {
                                    "text": "Thinking step...",
                                    "type": "summary_text",
                                },
                            ],
                            "type": "reasoning",
                        },
                    ),
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Answer",
                    ),
                ],
            ),
        )

    async def test_reasoning_raw_item_round_trip(self) -> None:
        """Encrypted reasoning metadata survives parsing and formatting."""
        reasoning_item_raw = {
            "id": "rs_encrypted",
            "summary": [
                {
                    "text": "Thinking step...",
                    "type": "summary_text",
                },
            ],
            "type": "reasoning",
            "content": [],
            "encrypted_content": "encrypted_payload",
            "status": "completed",
        }
        reasoning_item = _MockReasoningItem.model_validate(
            reasoning_item_raw,
        )
        mock_create = AsyncMock(
            return_value=_mock_completion(
                text="Answer",
                reasoning_output_item=reasoning_item,
            ),
        )
        self.mock_client.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        created_at=A,
                        thinking="Thinking step...",
                        reasoning_item_id="rs_encrypted",
                        reasoning_item_raw=reasoning_item_raw,
                    ),
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Answer",
                    ),
                ],
            ),
        )

        msg = AssistantMsg(
            name="assistant",
            content=result.content,
        )
        restored_msg = Msg.model_validate(msg.model_dump())
        self.assertEqual(restored_msg.content, result.content)

        formatted = await self.model.formatter.format(
            [restored_msg],
        )
        self.assertListEqual(
            formatted,
            [
                reasoning_item_raw,
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Answer",
                        },
                    ],
                },
            ],
        )

    async def test_reasoning_raw_item_excludes_none_fields(self) -> None:
        """Optional null SDK fields are not stored for history replay."""
        reasoning_item = _MockReasoningItem.model_validate(
            {
                "id": "rs_without_nulls",
                "summary": [],
                "type": "reasoning",
                "encrypted_content": "encrypted_payload",
            },
        )
        mock_create = AsyncMock(
            return_value=_mock_completion(
                reasoning_output_item=reasoning_item,
            ),
        )
        self.mock_client.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            result.content,
            [
                ThinkingBlock.model_construct(
                    id=A,
                    created_at=A,
                    thinking="",
                    reasoning_item_id="rs_without_nulls",
                    reasoning_item_raw={
                        "id": "rs_without_nulls",
                        "summary": [],
                        "type": "reasoning",
                        "encrypted_content": "encrypted_payload",
                    },
                ),
            ],
        )

    async def test_empty_reasoning_summary_response(
        self,
    ) -> None:
        """Non-stream empty reasoning summary still preserves its item id."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                reasoning_summary=[],
                text="Answer",
                reasoning_id="rs_empty",
            ),
        )
        self.mock_client.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        created_at=A,
                        thinking="",
                        reasoning_item_id="rs_empty",
                        reasoning_item_raw={
                            "id": "rs_empty",
                            "summary": [],
                            "type": "reasoning",
                        },
                    ),
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Answer",
                    ),
                ],
            ),
        )


class TestOpenAIResponseModelParameters(unittest.TestCase):
    """Tests for OpenAIResponseModel.Parameters."""

    def test_thinking_enable_stored_on_model(self) -> None:
        """thinking_enable is accessible through model.parameters."""
        model = OpenAIResponseModel(
            credential=OpenAICredential(api_key="test"),
            model="o4-mini",
            stream=False,
            context_size=200_000,
            parameters=OpenAIResponseModel.Parameters(thinking_enable=True),
        )
        self.assertTrue(model.parameters.thinking_enable)

    def test_reasoning_effort_stored_on_model(self) -> None:
        """reasoning_effort is accessible through model.parameters."""
        model = OpenAIResponseModel(
            credential=OpenAICredential(api_key="test"),
            model="o4-mini",
            stream=False,
            context_size=200_000,
            parameters=OpenAIResponseModel.Parameters(
                reasoning_effort="high",
            ),
        )
        self.assertEqual(model.parameters.reasoning_effort, "high")


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestOpenAIResponseStream(IsolatedAsyncioTestCase):
    """Tests for OpenAIResponseModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_stream_text(self) -> None:
        """Stream text yields deltas then final with full content."""
        completed_resp = MagicMock()
        completed_resp.id = "resp-1"
        completed_resp.output = []
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.output_text.delta",
                delta="Hello",
                response=MagicMock(id="resp-1"),
            ),
            _make_event(
                "response.output_text.delta",
                delta=" world",
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        stream = _MockAsyncEventStream(events)
        mock_create = AsyncMock(return_value=stream)
        self.mock_client.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertTrue(stream.exited)

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Hello",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text=" world",
                        ),
                    ],
                ),
                (
                    True,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Hello world",
                        ),
                    ],
                ),
            ],
        )

    async def test_stream_reasoning_and_text(
        self,
    ) -> None:
        """Stream reasoning and text deltas then final with
        reasoning_item_id."""
        reasoning_item_raw = {
            "id": "rs_123",
            "summary": [],
            "type": "reasoning",
        }
        reasoning_item = _MockReasoningItem.model_validate(
            reasoning_item_raw,
        )

        completed_resp = MagicMock()
        completed_resp.id = "resp-2"
        completed_resp.output = [reasoning_item]
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.reasoning_summary_text.delta",
                delta="Thinking",
                item_id="rs_123",
                response=MagicMock(id="resp-2"),
            ),
            _make_event(
                "response.output_text.delta",
                delta="Answer",
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        self.mock_client.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="Thinking",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Answer",
                        ),
                    ],
                ),
                # ``reasoning_item_id`` is only known at
                # ``response.completed``; it is emitted as a dedicated
                # carrier delta chunk (empty thinking text) that the base
                # accumulator merges onto the existing ``ThinkingBlock``.
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="",
                            reasoning_item_id="rs_123",
                            reasoning_item_raw=reasoning_item_raw,
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="Thinking",
                            reasoning_item_id="rs_123",
                            reasoning_item_raw=reasoning_item_raw,
                        ),
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Answer",
                        ),
                    ],
                ),
            ],
        )

    async def test_stream_preserves_multiple_reasoning_items(self) -> None:
        """Streaming keeps every reasoning item's encrypted payload."""
        first_raw = {
            "id": "rs_first",
            "summary": [],
            "type": "reasoning",
            "encrypted_content": "encrypted_first",
        }
        second_raw = {
            "id": "rs_second",
            "summary": [],
            "type": "reasoning",
            "encrypted_content": "encrypted_second",
        }
        completed_resp = MagicMock()
        completed_resp.id = "resp-multiple-reasoning"
        completed_resp.output = [
            _MockReasoningItem.model_validate(first_raw),
            _MockReasoningItem.model_validate(second_raw),
        ]
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.reasoning_summary_text.delta",
                delta="First",
                item_id="rs_first",
            ),
            _make_event(
                "response.reasoning_summary_text.delta",
                delta="Second",
                item_id="rs_second",
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        self.mock_client.responses.create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )

        gen = await self.model([])
        responses = [response async for response in gen]

        self.assertEqual(
            (responses[-1].is_last, responses[-1].content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        created_at=A,
                        thinking="First",
                        reasoning_item_id="rs_first",
                        reasoning_item_raw=first_raw,
                    ),
                    ThinkingBlock.model_construct(
                        id=A,
                        created_at=A,
                        thinking="Second",
                        reasoning_item_id="rs_second",
                        reasoning_item_raw=second_raw,
                    ),
                ],
            ),
        )

        formatted = await self.model.formatter.format(
            [
                AssistantMsg(
                    name="assistant",
                    content=responses[-1].content,
                ),
            ],
        )
        self.assertListEqual(formatted, [first_raw, second_raw])

    async def test_stream_empty_reasoning_summary_keeps_reasoning_item_id(
        self,
    ) -> None:
        """Stream empty reasoning summary still preserves its item id."""
        reasoning_item_raw = {
            "id": "rs_empty",
            "summary": [],
            "type": "reasoning",
            "encrypted_content": "encrypted_stream_payload",
        }
        reasoning_item = _MockReasoningItem.model_validate(
            reasoning_item_raw,
        )

        msg_item = MagicMock()
        msg_item.type = "message"

        completed_resp = MagicMock()
        completed_resp.id = "resp-empty"
        completed_resp.output = [reasoning_item, msg_item]
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.output_text.delta",
                delta="Answer",
                response=MagicMock(id="resp-empty"),
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        self.mock_client.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Answer",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="",
                            reasoning_item_id="rs_empty",
                            reasoning_item_raw=reasoning_item_raw,
                        ),
                    ],
                ),
                (
                    True,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Answer",
                        ),
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="",
                            reasoning_item_id="rs_empty",
                            reasoning_item_raw=reasoning_item_raw,
                        ),
                    ],
                ),
            ],
        )

    async def test_stream_function_call(
        self,
    ) -> None:
        """Stream function-call events use call_id as ToolCallBlock.id."""
        fc_item = MagicMock()
        fc_item.type = "function_call"
        fc_item.id = "fc_1"
        fc_item.call_id = "call-1"
        fc_item.name = "search"

        completed_resp = MagicMock()
        completed_resp.id = "resp-3"
        completed_resp.output = []
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.output_item.added",
                item=fc_item,
                response=MagicMock(id="resp-3"),
            ),
            _make_event(
                "response.function_call_arguments.delta",
                item_id="fc_1",
                delta='{"q":',
            ),
            _make_event(
                "response.function_call_arguments.delta",
                item_id="fc_1",
                delta='"test"}',
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        self.mock_client.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        ToolCallBlock.model_construct(
                            id="call-1",
                            created_at=A,
                            name="search",
                            input='{"q":',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock.model_construct(
                            id="call-1",
                            created_at=A,
                            name="search",
                            input='"test"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock.model_construct(
                            id="call-1",
                            created_at=A,
                            name="search",
                            input='{"q":"test"}',
                        ),
                    ],
                ),
            ],
        )


# ---------------------------------------------------------------------------
# _format_tools tests
# ---------------------------------------------------------------------------

_FT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the time",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string"}},
                "required": ["timezone"],
            },
        },
    },
]

_FT_TOOLS_RESPONSE = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Get the weather",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "type": "function",
        "name": "get_time",
        "description": "Get the time",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
            "required": ["timezone"],
        },
    },
]


class TestOpenAIResponseFormatTools(unittest.TestCase):
    """Tests for OpenAIResponseModel._format_tools."""

    def setUp(self) -> None:
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode converts tools and sets choice to 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """None mode converts tools and sets choice to 'none'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "none")

    def test_required_mode(self) -> None:
        """Required mode converts tools and sets choice to 'required'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="required"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "required")

    def test_str_mode_force_call(self) -> None:
        """String mode forces a function call for the named tool."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(
            fmt_choice,
            {"type": "function", "name": "get_weather"},
        )

    def test_tools_filtered(self) -> None:
        """ToolChoice with tools list keeps the full tools schema and
        narrows the callable subset via ``allowed_tools`` to preserve
        prompt cache hits."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertListEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(
            fmt_choice,
            {
                "type": "allowed_tools",
                "mode": "auto",
                "tools": [{"type": "function", "name": "get_weather"}],
            },
        )

    def test_no_tool_choice(self) -> None:
        """Tools are converted when tool_choice is None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertIsNone(fmt_choice)
