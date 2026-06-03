# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for MiniMaxChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes, verifying that:
- Non-stream mode returns a single ChatResponse with is_last=True.
- Stream mode yields n delta ChatResponses (is_last=False) followed by
  1 final ChatResponse (is_last=True) with the full accumulated content.
- Model card listing returns the expected MiniMax models.
"""
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import MiniMaxChatModel
from agentscope.credential import MiniMaxCredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return MiniMaxChatModel(
        credential=MiniMaxCredential(api_key="test"),
        model="MiniMax-M3",
        stream=stream,
        context_size=512_000,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    reasoning: Any = None,
    response_id: str = "minimax-1",
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
    resp.usage.prompt_tokens_details = None
    return resp


def _make_stream_chunk(
    delta_text: str | None = None,
    delta_reasoning: str | None = None,
    tool_calls: list | None = None,
    response_id: str = "minimax-1",
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
        chunk.usage.prompt_tokens_details = None
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
    id_: str | None,
    name: str | None,
    arguments: str,
) -> MagicMock:
    """Build a single tool-call delta for stream chunks."""
    tc = MagicMock()
    tc.index = index
    tc.id = id_
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


class _MockAsyncStream:
    """Minimal async context-manager + iterator that yields prebuilt chunks."""

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


class TestMiniMaxNonStream(IsolatedAsyncioTestCase):
    """Tests for MiniMaxChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("openai.AsyncClient")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello!")]),
        )
        self.assertEqual(result.id, "minimax-1")

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
                        "arguments": '{"city":"Shanghai"}',
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
                        input='{"city":"Shanghai"}',
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
                text="42",
                reasoning="Step by step...",
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
                        thinking="Step by step...",
                    ),
                    TextBlock.model_construct(id=A, text="42"),
                ],
            ),
        )


class TestMiniMaxModelParameters(unittest.TestCase):
    """Tests for MiniMaxChatModel.Parameters."""

    def test_default_temperature_is_one(self) -> None:
        """Default temperature is 1.0 (MiniMax requires non-zero)."""
        params = MiniMaxChatModel.Parameters()
        self.assertEqual(params.temperature, 1.0)

    def test_temperature_must_be_positive(self) -> None:
        """Zero or negative temperature is rejected (range (0.0, 1.0])."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            MiniMaxChatModel.Parameters(temperature=0.0)

        with self.assertRaises(ValidationError):
            MiniMaxChatModel.Parameters(temperature=1.5)

    def test_default_base_url_is_minimax(self) -> None:
        """Default base URL points to the overseas MiniMax endpoint."""
        cred = MiniMaxCredential(api_key="test")
        self.assertEqual(cred.base_url, "https://api.minimax.io/v1")

    def test_temperature_override_is_stored(self) -> None:
        """Explicit temperature override is honored."""
        model = MiniMaxChatModel(
            credential=MiniMaxCredential(api_key="test"),
            model="MiniMax-M3",
            stream=False,
            context_size=512_000,
            parameters=MiniMaxChatModel.Parameters(temperature=0.7),
        )
        self.assertEqual(model.parameters.temperature, 0.7)


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestMiniMaxStream(IsolatedAsyncioTestCase):
    """Tests for MiniMaxChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("openai.AsyncClient")
    async def test_stream_text(self, mock_client_cls: MagicMock) -> None:
        """Stream text yields n deltas (is_last=False) + 1 final
        (is_last=True) with full content."""
        chunks = [
            _make_stream_chunk(delta_text="Hi"),
            _make_stream_chunk(delta_text=" there"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 2},
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
                (False, [TextBlock.model_construct(id=A, text=" there")]),
                (True, [TextBlock.model_construct(id=A, text="Hi there")]),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_thinking_then_text(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """MiniMax yields thinking chunks separately before text."""
        chunks = [
            _make_stream_chunk(delta_reasoning="Let me"),
            _make_stream_chunk(delta_reasoning=" think"),
            _make_stream_chunk(delta_text="Result"),
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
                    [ThinkingBlock.model_construct(id=A, thinking="Let me")],
                ),
                (
                    False,
                    [ThinkingBlock.model_construct(id=A, thinking=" think")],
                ),
                (False, [TextBlock.model_construct(id=A, text="Result")]),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Let me think",
                        ),
                        TextBlock.model_construct(id=A, text="Result"),
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
                tool_calls=[
                    _make_tool_call_delta(0, "call-1", "search", '{"q":'),
                ],
            ),
            _make_stream_chunk(
                tool_calls=[
                    _make_tool_call_delta(0, None, None, '"test"}'),
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
                            name="search",
                            input='{"q":',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="search",
                            input='"test"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock(
                            id="call-1",
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


class TestMiniMaxFormatTools(unittest.TestCase):
    """Tests for MiniMaxChatModel._format_tools."""

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


# ---------------------------------------------------------------------------
# Model card listing tests
# ---------------------------------------------------------------------------


class TestMiniMaxModelListing(unittest.TestCase):
    """Tests for MiniMax model card discovery."""

    def test_list_models_returns_minimax_cards(self) -> None:
        """list_models() returns the three MiniMax model cards."""
        cards = MiniMaxChatModel.list_models()
        names = {c.name for c in cards}
        self.assertIn("MiniMax-M3", names)
        self.assertIn("MiniMax-M2.7", names)
        self.assertIn("MiniMax-M2.7-highspeed", names)

    def test_m3_is_active(self) -> None:
        """The M3 model card is marked active."""
        cards = MiniMaxChatModel.list_models()
        m3 = next(c for c in cards if c.name == "MiniMax-M3")
        self.assertEqual(m3.status, "active")

    def test_m3_supports_image_input(self) -> None:
        """The M3 model card advertises image/* input support."""
        cards = MiniMaxChatModel.list_models()
        m3 = next(c for c in cards if c.name == "MiniMax-M3")
        image_inputs = [t for t in m3.input_types if t.startswith("image/")]
        self.assertGreater(len(image_inputs), 0)

    def test_credential_lists_models(self) -> None:
        """MiniMaxCredential.list_models delegates to the chat model class."""
        cards = MiniMaxCredential.list_models()
        names = {c.name for c in cards}
        self.assertIn("MiniMax-M3", names)


if __name__ == "__main__":
    unittest.main()
