# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for DeepSeekChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes, verifying that:
- Non-stream mode returns a single ChatResponse with is_last=True.
- Stream mode yields n delta ChatResponses (is_last=False) followed by
  1 final ChatResponse (is_last=True) with the full accumulated content.
"""
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import DeepSeekChatModel
from agentscope.credential import DeepSeekCredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return DeepSeekChatModel(
        credential=DeepSeekCredential(api_key="test"),
        model="deepseek-v4-pro",
        stream=stream,
        context_size=65_536,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    reasoning: Any = None,
    response_id: str = "deepseek-1",
) -> MagicMock:
    """Build a mock non-streaming ChatCompletion response."""
    msg = MagicMock()
    msg.content = text
    msg.reasoning_content = reasoning
    msg.tool_calls = None

    if tool_calls:
        tc_mocks = []
        for tc in tool_calls:
            m = MagicMock()
            m.id = tc["id"]
            m.function.name = tc["name"]
            m.function.arguments = tc["arguments"]
            tc_mocks.append(m)
        msg.tool_calls = tc_mocks

    choice = MagicMock()
    choice.message = msg

    resp = MagicMock()
    resp.id = response_id
    resp.choices = [choice]
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.prompt_cache_hit_tokens = 0
    return resp


def _make_stream_chunk(
    delta_text: str | None = None,
    delta_reasoning: str | None = None,
    tool_calls: list | None = None,
    response_id: str = "deepseek-1",
    usage: dict | None = None,
    has_choices: bool = True,
) -> MagicMock:
    """Build a single mock streaming chunk."""
    chunk = MagicMock()
    chunk.id = response_id

    if usage:
        chunk.usage = MagicMock()
        chunk.usage.prompt_tokens = usage.get("prompt_tokens", 0)
        chunk.usage.completion_tokens = usage.get("completion_tokens", 0)
        chunk.usage.prompt_cache_hit_tokens = usage.get(
            "prompt_cache_hit_tokens",
            0,
        )
    else:
        chunk.usage = None

    if has_choices:
        delta = MagicMock()
        delta.content = delta_text
        delta.reasoning_content = delta_reasoning
        delta.tool_calls = tool_calls
        choice = MagicMock()
        choice.delta = delta
        chunk.choices = [choice]
    else:
        chunk.choices = []

    return chunk


def _make_tool_call_delta(
    index: int,
    tc_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> MagicMock:
    """Build a tool_call delta item for streaming."""
    tc = MagicMock()
    tc.index = index
    tc.id = tc_id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


class _MockAsyncStream:
    """Mock async stream that acts as an async context manager + iterator."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self._index = 0

    async def __aenter__(self) -> "_MockAsyncStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def __aiter__(self) -> "_MockAsyncStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


# ---------------------------------------------------------------------------
# Non-streaming tests
# ---------------------------------------------------------------------------


class TestDeepSeekNonStream(IsolatedAsyncioTestCase):
    """Tests for DeepSeekChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("openai.AsyncClient")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello world!"),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello world!")]),
        )
        self.assertEqual(result.id, "deepseek-1")

    @patch("openai.AsyncClient")
    async def test_tool_call_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream tool call response creates ToolCallBlocks."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "get_weather",
                        "arguments": '{"city":"Beijing"}',
                    },
                    {
                        "id": "call-2",
                        "name": "get_time",
                        "arguments": '{"tz":"UTC"}',
                    },
                ],
            ),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock(
                        id="call-1",
                        name="get_weather",
                        input='{"city":"Beijing"}',
                    ),
                    ToolCallBlock(
                        id="call-2",
                        name="get_time",
                        input='{"tz":"UTC"}',
                    ),
                ],
            ),
        )

    @patch("openai.AsyncClient")
    async def test_thinking_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream response with reasoning creates ThinkingBlock."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                text="The answer is 42.",
                reasoning="Let me think step by step...",
            ),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        thinking="Let me think step by step...",
                    ),
                    TextBlock.model_construct(id=A, text="The answer is 42."),
                ],
            ),
        )


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestDeepSeekStream(IsolatedAsyncioTestCase):
    """Tests for DeepSeekChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("openai.AsyncClient")
    async def test_stream_text_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream text yields n deltas (is_last=False) + 1 final
        (is_last=True) with full content."""
        chunks = [
            _make_stream_chunk(delta_text="Hello"),
            _make_stream_chunk(delta_text=" world"),
            _make_stream_chunk(delta_text="!"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 3},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [TextBlock.model_construct(id=A, text="Hello")]),
                (False, [TextBlock.model_construct(id=A, text=" world")]),
                (False, [TextBlock.model_construct(id=A, text="!")]),
                (True, [TextBlock.model_construct(id=A, text="Hello world!")]),
            ],
        )
        self.assertEqual(responses[-1].id, "deepseek-1")

    @patch("openai.AsyncClient")
    async def test_stream_thinking_and_text(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream with thinking + text yields deltas then final with both."""
        chunks = [
            _make_stream_chunk(delta_reasoning="Think"),
            _make_stream_chunk(delta_reasoning="ing..."),
            _make_stream_chunk(delta_text="Answer"),
            _make_stream_chunk(delta_text=" here."),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 8},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [ThinkingBlock.model_construct(id=A, thinking="Think")],
                ),
                (
                    False,
                    [ThinkingBlock.model_construct(id=A, thinking="ing...")],
                ),
                (False, [TextBlock.model_construct(id=A, text="Answer")]),
                (False, [TextBlock.model_construct(id=A, text=" here.")]),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Thinking...",
                        ),
                        TextBlock.model_construct(id=A, text="Answer here."),
                    ],
                ),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_tool_calls(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream tool calls accumulate across chunks into final response."""
        chunks = [
            _make_stream_chunk(
                delta_text=None,
                tool_calls=[
                    _make_tool_call_delta(0, "call-1", "get_weather", '{"ci'),
                ],
            ),
            _make_stream_chunk(
                delta_text=None,
                tool_calls=[
                    _make_tool_call_delta(0, None, None, 'ty":"BJ"}'),
                ],
            ),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="get_weather",
                            input='{"ci',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="get_weather",
                            input='ty":"BJ"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="get_weather",
                            input='{"city":"BJ"}',
                        ),
                    ],
                ),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_text_then_tool_call(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream with text followed by tool call accumulates both."""
        chunks = [
            _make_stream_chunk(delta_reasoning="Let me check"),
            _make_stream_chunk(delta_text="I'll look it up."),
            _make_stream_chunk(
                delta_text=None,
                tool_calls=[
                    _make_tool_call_delta(
                        0,
                        "call-1",
                        "search",
                        '{"q":"weather"}',
                    ),
                ],
            ),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 15, "completion_tokens": 10},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

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
                            thinking="Let me check",
                        ),
                    ],
                ),
                (
                    False,
                    [TextBlock.model_construct(id=A, text="I'll look it up.")],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="search",
                            input='{"q":"weather"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Let me check",
                        ),
                        TextBlock.model_construct(
                            id=A,
                            text="I'll look it up.",
                        ),
                        ToolCallBlock(
                            id="call-1",
                            name="search",
                            input='{"q":"weather"}',
                        ),
                    ],
                ),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_usage_in_final(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Usage information is captured and present in final response."""
        chunks = [
            _make_stream_chunk(delta_text="Hi"),
            _make_stream_chunk(
                has_choices=False,
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 50,
                },
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [TextBlock.model_construct(id=A, text="Hi")]),
                (True, [TextBlock.model_construct(id=A, text="Hi")]),
            ],
        )
        self.assertEqual(responses[-1].usage.input_tokens, 100)
        self.assertEqual(responses[-1].usage.output_tokens, 20)
        self.assertEqual(responses[-1].usage.cache_input_tokens, 50)


class TestDeepSeekModelParameters(unittest.TestCase):
    """Tests for DeepSeekChatModel.Parameters."""

    def test_thinking_enable_stored_on_model(self) -> None:
        """thinking_enable is accessible through model.parameters."""
        model = DeepSeekChatModel(
            credential=DeepSeekCredential(api_key="test"),
            model="deepseek-reasoner",
            stream=False,
            context_size=65_536,
            parameters=DeepSeekChatModel.Parameters(thinking_enable=True),
        )
        self.assertTrue(model.parameters.thinking_enable)

    def test_reasoning_effort_stored_on_model(self) -> None:
        """reasoning_effort is accessible through model.parameters."""
        model = DeepSeekChatModel(
            credential=DeepSeekCredential(api_key="test"),
            model="deepseek-reasoner",
            stream=False,
            context_size=65_536,
            parameters=DeepSeekChatModel.Parameters(reasoning_effort="max"),
        )
        self.assertEqual(model.parameters.reasoning_effort, "max")


# ---------------------------------------------------------------------------
# Shared _format_tools fixtures
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


class TestDeepSeekFormatTools(unittest.TestCase):
    """Tests for DeepSeekChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode returns tools unchanged and string 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """None mode returns tools unchanged and string 'none'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "none")

    def test_required_mode(self) -> None:
        """Required mode returns tools unchanged and string 'required'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="required"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "required")

    def test_str_mode_force_call(self) -> None:
        """A specific tool name returns a type=function dict."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(
            fmt_choice,
            {"type": "function", "function": {"name": "get_weather"}},
        )

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["function"]["name"], "get_weather")
        self.assertEqual(fmt_choice, "auto")

    def test_no_tool_choice(self) -> None:
        """Without tool_choice, returns tools and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertIsNone(fmt_choice)
