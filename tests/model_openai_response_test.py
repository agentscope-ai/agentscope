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

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import OpenAIResponseModel
from agentscope.credential import OpenAICredential
from agentscope.tool import ToolChoice

A = AnyString()


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
) -> MagicMock:
    """Build a mock non-streaming Responses API response."""
    output = []

    if reasoning_summary is not None:
        reasoning_item = MagicMock()
        reasoning_item.type = "reasoning"
        reasoning_item.id = reasoning_id
        summary_texts = (
            reasoning_summary
            if isinstance(reasoning_summary, list)
            else [reasoning_summary]
            if reasoning_summary is not None
            else []
        )
        reasoning_item.summary = []
        for summary_text in summary_texts:
            summary_mock = MagicMock()
            summary_mock.text = summary_text
            reasoning_item.summary.append(summary_mock)
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
                    ),
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Answer",
                    ),
                ],
            ),
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
        reasoning_item = MagicMock()
        reasoning_item.type = "reasoning"
        reasoning_item.id = "rs_123"

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

    async def test_stream_empty_reasoning_summary_keeps_reasoning_item_id(
        self,
    ) -> None:
        """Stream empty reasoning summary still preserves its item id."""
        reasoning_item = MagicMock()
        reasoning_item.type = "reasoning"
        reasoning_item.id = "rs_empty"

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
                        ),
                    ],
                ),
            ],
        )

    async def test_stream_multiple_reasoning_items(
        self,
    ) -> None:
        """Stream with multiple reasoning items preserves each item's id."""
        reasoning_item_1 = MagicMock()
        reasoning_item_1.type = "reasoning"
        reasoning_item_1.id = "rs_first"

        reasoning_item_2 = MagicMock()
        reasoning_item_2.type = "reasoning"
        reasoning_item_2.id = "rs_second"

        fc_item = MagicMock()
        fc_item.type = "function_call"
        fc_item.id = "fc_1"
        fc_item.call_id = "call-1"
        fc_item.name = "search"

        completed_resp = MagicMock()
        completed_resp.id = "resp-multi"
        completed_resp.output = [reasoning_item_1, reasoning_item_2]
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.reasoning_summary_text.delta",
                delta="First thought",
                item_id="rs_first",
                response=MagicMock(id="resp-multi"),
            ),
            _make_event(
                "response.reasoning_summary_text.delta",
                delta="Second thought",
                item_id="rs_second",
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        self.mock_client.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        # Final accumulated response should have two separate
        # ThinkingBlocks, each with its own reasoning_item_id.
        final = responses[-1]
        thinking_blocks = [
            b for b in final.content if hasattr(b, "reasoning_item_id")
        ]
        self.assertEqual(len(thinking_blocks), 2)
        ids = {b.reasoning_item_id for b in thinking_blocks}
        self.assertEqual(ids, {"rs_first", "rs_second"})

    async def test_stream_interleaved_reasoning_and_function_calls(
        self,
    ) -> None:
        """Interleaved reasoning/function_call items keep their order.

        Exercises the exact sequence reported by the review:
        ``reasoning_1 -> function_call_1 -> reasoning_2 -> function_call_2``.
        The reasoning blocks must be created on their ``output_item.added``
        events (so they sit *before* the following function-call blocks),
        and passing the resulting assistant message through
        ``OpenAIResponseFormatter`` must replay the items in that same order.
        """
        reasoning_item_1 = MagicMock()
        reasoning_item_1.type = "reasoning"
        reasoning_item_1.id = "rs_1"

        fc_item_1 = MagicMock()
        fc_item_1.type = "function_call"
        fc_item_1.id = "fc_1"
        fc_item_1.call_id = "call-1"
        fc_item_1.name = "search"

        reasoning_item_2 = MagicMock()
        reasoning_item_2.type = "reasoning"
        reasoning_item_2.id = "rs_2"

        fc_item_2 = MagicMock()
        fc_item_2.type = "function_call"
        fc_item_2.id = "fc_2"
        fc_item_2.call_id = "call-2"
        fc_item_2.name = "update"

        completed_resp = MagicMock()
        completed_resp.id = "resp-interleaved"
        completed_resp.output = [
            reasoning_item_1,
            fc_item_1,
            reasoning_item_2,
            fc_item_2,
        ]
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            # reasoning_1 announced first -> thinking block "rs_1"
            _make_event(
                "response.output_item.added",
                item=reasoning_item_1,
                response=MagicMock(id="resp-interleaved"),
            ),
            _make_event(
                "response.reasoning_summary_text.delta",
                delta="First thought",
                item_id="rs_1",
            ),
            # function_call_1 announced -> tool_call_mapping recorded
            _make_event("response.output_item.added", item=fc_item_1),
            _make_event(
                "response.function_call_arguments.delta",
                item_id="fc_1",
                delta='{"q":"a"}',
            ),
            # reasoning_2 announced -> thinking block "rs_2"
            _make_event("response.output_item.added", item=reasoning_item_2),
            _make_event(
                "response.reasoning_summary_text.delta",
                delta="Second thought",
                item_id="rs_2",
            ),
            # function_call_2 announced
            _make_event("response.output_item.added", item=fc_item_2),
            _make_event(
                "response.function_call_arguments.delta",
                item_id="fc_2",
                delta='{"k":"v"}',
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        self.mock_client.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        final = responses[-1]

        # 1) Assert the full ordered ``content`` structure mirrors
        #    reasoning_1 -> function_call_1 -> reasoning_2 -> function_call_2.
        types = [
            (
                "thinking" if isinstance(b, ThinkingBlock) else "tool",
                getattr(b, "reasoning_item_id", None),
                getattr(b, "id", None),
            )
            for b in final.content
        ]
        self.assertListEqual(
            types,
            [
                ("thinking", "rs_1", "rs_1"),
                ("tool", None, "call-1"),
                ("thinking", "rs_2", "rs_2"),
                ("tool", None, "call-2"),
            ],
        )

        # 2) The accumulated assistant message, when replayed through the
        #    OpenAI Responses formatter, must preserve the item order.
        from agentscope.message import AssistantMsg
        from agentscope.formatter import OpenAIResponseFormatter

        replay_msg = AssistantMsg(
            name="assistant",
            content=[*final.content],
        )
        fmt = OpenAIResponseFormatter()
        formatted = await fmt.format([replay_msg])
        # The replayed sequence starts with reasoning_1 then function_call_1,
        # then reasoning_2 then function_call_2.
        top_level = []
        for item in formatted:
            if item.get("type") in ("reasoning", "function_call"):
                top_level.append(
                    (item.get("type"), item.get("id") or item.get("call_id")),
                )

        self.assertListEqual(
            top_level,
            [
                ("reasoning", "rs_1"),
                ("function_call", "call-1"),
                ("reasoning", "rs_2"),
                ("function_call", "call-2"),
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
