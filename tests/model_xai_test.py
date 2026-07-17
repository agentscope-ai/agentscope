# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for XAIChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
XAI uses xai_sdk with chat.stream() for streaming.
"""
import sys
from typing import Any
from types import ModuleType
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import XAIChatModel
from agentscope.credential import XAICredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Build a lightweight xai_sdk stub so tests run without the real package.
# ---------------------------------------------------------------------------


def _build_xai_sdk_stub() -> None:
    """Register stub modules for xai_sdk so imports don't fail."""
    if "xai_sdk" in sys.modules:
        return

    chat_pb2 = ModuleType("xai_sdk.chat.chat_pb2")

    class _EnumHelper:
        """Helper that makes .Value() return an integer."""

        _mapping = {
            "ROLE_ASSISTANT": 2,
            "TOOL_CALL_TYPE_CLIENT_SIDE_TOOL": 1,
        }

        def Value(self, name: str) -> int:
            """Return the integer value for the given enum name."""
            return self._mapping.get(name, 0)

    chat_pb2.MessageRole = _EnumHelper()
    chat_pb2.ToolCallType = _EnumHelper()

    class _RepeatedField(list):
        """Minimal repeated proto field that supports .add()."""

        def __init__(self, factory: Any) -> None:
            super().__init__()
            self._factory = factory

        def add(self) -> Any:
            """Add a new item using the factory and return it."""
            item = self._factory()
            self.append(item)
            return item

    class _FunctionSpec:
        name: str = ""
        arguments: str = ""

    class _ToolCallProto:
        id: str = ""
        type: int = 0
        function = _FunctionSpec()

    class _ContentPart:
        text: str = ""

    class _MessageProto:
        def __init__(self) -> None:
            self.role = 0
            self.content = _RepeatedField(_ContentPart)
            self.tool_calls = _RepeatedField(_ToolCallProto)

    chat_pb2.Message = _MessageProto

    xai_chat = ModuleType("xai_sdk.chat")
    xai_chat.chat_pb2 = chat_pb2
    xai_chat.user = lambda *args: MagicMock(role="user", args=args)
    xai_chat.assistant = lambda *args: MagicMock(role="assistant", args=args)
    xai_chat.system = lambda *args: MagicMock(role="system", args=args)
    xai_chat.tool_result = lambda *args, **kw: MagicMock(
        role="tool",
        args=args,
        kwargs=kw,
    )
    xai_chat.image = lambda url: MagicMock(type="image", url=url)

    xai_sdk = ModuleType("xai_sdk")
    xai_sdk.chat = xai_chat
    xai_sdk.AsyncClient = MagicMock()

    sys.modules["xai_sdk"] = xai_sdk
    sys.modules["xai_sdk.chat"] = xai_chat
    sys.modules["xai_sdk.chat.chat_pb2"] = chat_pb2


_build_xai_sdk_stub()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return XAIChatModel(
        credential=XAICredential(api_key="test"),
        model="grok-3",
        stream=stream,
        context_size=131_072,
    )


def _mock_completion(
    text: str = "",
    reasoning: str = "",
    tool_calls: list | None = None,
    response_id: str = "xai-resp-1",
) -> MagicMock:
    """Build a mock xAI non-streaming response."""
    resp = MagicMock()
    resp.id = response_id
    resp.content = text
    resp.reasoning_content = reasoning
    resp.tool_calls = None
    resp.usage = None

    if tool_calls:
        tc_mocks = []
        for tc in tool_calls:
            m = MagicMock()
            m.id = tc["id"]
            m.function.name = tc["name"]
            m.function.arguments = tc["arguments"]
            tc_mocks.append(m)
        resp.tool_calls = tc_mocks

    return resp


class _MockStreamChunk:
    """A single chunk from xai chat.stream()."""

    def __init__(
        self,
        content: str = "",
        reasoning_content: str = "",
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content


class _MockChatStream:
    """Mock xai_sdk chat session with stream() and sample()."""

    def __init__(
        self,
        stream_items: list | None = None,
        sample_response: Any = None,
    ) -> None:
        self._stream_items = stream_items or []
        self._sample_response = sample_response
        self._appended: list = []

    def append(self, msg: Any) -> None:
        """Append a message to the conversation."""
        self._appended.append(msg)

    async def sample(self) -> Any:
        """Return the pre-configured sample response."""
        return self._sample_response

    def stream(self) -> "_MockStreamIterator":
        """Return an async iterator over pre-configured stream items."""
        return _MockStreamIterator(self._stream_items)


class _MockStreamIterator:
    """Async iterator for (response, chunk) pairs from xai stream."""

    def __init__(self, items: list) -> None:
        self._items = items
        self._index = 0

    def __aiter__(self) -> "_MockStreamIterator":
        return self

    async def __anext__(self) -> tuple:
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


# ---------------------------------------------------------------------------
# Non-streaming tests
# ---------------------------------------------------------------------------


class TestXAINonStream(IsolatedAsyncioTestCase):
    """Tests for XAIChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("xai_sdk.AsyncClient")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_chat = _MockChatStream(
            sample_response=_mock_completion(text="Hello!"),
        )
        mock_client_cls.return_value.chat.create.return_value = mock_chat
        mock_client_cls.return_value.close = AsyncMock()

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello!")]),
        )
        self.assertEqual(result.id, "xai-resp-1")

    @patch("xai_sdk.AsyncClient")
    async def test_tool_call_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Parsing a tool-call response creates a ToolCallBlock."""
        mock_chat = _MockChatStream(
            sample_response=_mock_completion(
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "get_weather",
                        "arguments": '{"city":"NY"}',
                    },
                ],
            ),
        )
        mock_client_cls.return_value.chat.create.return_value = mock_chat
        mock_client_cls.return_value.close = AsyncMock()

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock(
                        id="call-1",
                        name="get_weather",
                        input='{"city":"NY"}',
                    ),
                ],
            ),
        )

    @patch("xai_sdk.AsyncClient")
    async def test_thinking_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream reasoning plus text returns ThinkingBlock then
        TextBlock."""
        mock_chat = _MockChatStream(
            sample_response=_mock_completion(
                text="Answer",
                reasoning="Deep thinking...",
            ),
        )
        mock_client_cls.return_value.chat.create.return_value = mock_chat
        mock_client_cls.return_value.close = AsyncMock()

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        thinking="Deep thinking...",
                    ),
                    TextBlock.model_construct(id=A, text="Answer"),
                ],
            ),
        )


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestXAIStream(IsolatedAsyncioTestCase):
    """Tests for XAIChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("xai_sdk.AsyncClient")
    async def test_stream_text(self, mock_client_cls: MagicMock) -> None:
        """Stream text yields deltas then final with full content."""
        final_response = _mock_completion(text="Hello world")
        final_response.tool_calls = None
        final_response.usage = MagicMock()
        final_response.usage.prompt_tokens = 10
        final_response.usage.completion_tokens = 5
        final_response.usage.cached_prompt_text_tokens = 0

        stream_items = [
            (final_response, _MockStreamChunk(content="Hello")),
            (final_response, _MockStreamChunk(content=" world")),
        ]
        mock_chat = _MockChatStream(stream_items=stream_items)
        mock_client_cls.return_value.chat.create.return_value = mock_chat
        mock_client_cls.return_value.close = AsyncMock()

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [TextBlock.model_construct(id=A, text="Hello")]),
                (False, [TextBlock.model_construct(id=A, text=" world")]),
                (True, [TextBlock.model_construct(id=A, text="Hello world")]),
            ],
        )

    @patch("xai_sdk.AsyncClient")
    async def test_stream_thinking_and_text(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream reasoning and text deltas then final with accumulated
        content."""
        final_response = _mock_completion(text="")
        final_response.tool_calls = None
        final_response.usage = MagicMock()
        final_response.usage.prompt_tokens = 10
        final_response.usage.completion_tokens = 5
        final_response.usage.cached_prompt_text_tokens = 0

        stream_items = [
            (final_response, _MockStreamChunk(reasoning_content="Think")),
            (final_response, _MockStreamChunk(content="Answer")),
        ]
        mock_chat = _MockChatStream(stream_items=stream_items)
        mock_client_cls.return_value.chat.create.return_value = mock_chat
        mock_client_cls.return_value.close = AsyncMock()

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [ThinkingBlock.model_construct(id=A, thinking="Think")],
                ),
                (False, [TextBlock.model_construct(id=A, text="Answer")]),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(id=A, thinking="Think"),
                        TextBlock.model_construct(id=A, text="Answer"),
                    ],
                ),
            ],
        )

    @patch("xai_sdk.AsyncClient")
    async def test_stream_tool_calls_in_final(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream text delta then final adds tool calls from last_response."""
        final_response = _mock_completion(
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "search",
                    "arguments": '{"q":"test"}',
                },
            ],
        )
        final_response.usage = MagicMock()
        final_response.usage.prompt_tokens = 10
        final_response.usage.completion_tokens = 5
        final_response.usage.cached_prompt_text_tokens = 0

        stream_items = [
            (final_response, _MockStreamChunk(content="I'll search")),
        ]
        mock_chat = _MockChatStream(stream_items=stream_items)
        mock_client_cls.return_value.chat.create.return_value = mock_chat
        mock_client_cls.return_value.close = AsyncMock()

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [TextBlock.model_construct(id=A, text="I'll search")]),
                # ``xai_sdk`` does not stream tool calls — they only
                # appear on the final accumulated response object, so
                # the parser emits them as a dedicated trailing carrier
                # delta that the base accumulator merges.
                (
                    False,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="search",
                            input='{"q":"test"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        TextBlock.model_construct(id=A, text="I'll search"),
                        ToolCallBlock(
                            id="call-1",
                            name="search",
                            input='{"q":"test"}',
                        ),
                    ],
                ),
            ],
        )


class TestXAIModelParameters(unittest.TestCase):
    """Tests for XAIChatModel.Parameters."""

    def test_thinking_enable_stored_on_model(self) -> None:
        """thinking_enable is accessible through model.parameters."""
        model = XAIChatModel(
            credential=XAICredential(api_key="test"),
            model="grok-3-mini",
            stream=False,
            context_size=131_072,
            parameters=XAIChatModel.Parameters(thinking_enable=True),
        )
        self.assertTrue(model.parameters.thinking_enable)

    def test_reasoning_effort_stored_on_model(self) -> None:
        """reasoning_effort is accessible through model.parameters."""
        model = XAIChatModel(
            credential=XAICredential(api_key="test"),
            model="grok-3-mini",
            stream=False,
            context_size=131_072,
            parameters=XAIChatModel.Parameters(reasoning_effort="high"),
        )
        self.assertEqual(model.parameters.reasoning_effort, "high")


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


def _extend_xai_stub_for_tools() -> None:
    """Add tool and required_tool stubs to the existing xai_sdk stub."""
    xai_chat = sys.modules.get("xai_sdk.chat")
    if xai_chat is None or hasattr(xai_chat, "required_tool"):
        return

    class _RequiredTool:
        def __init__(self, tool_name: str) -> None:
            self.tool_name = tool_name

        def __eq__(self, other: object) -> bool:
            return (
                isinstance(other, _RequiredTool)
                and self.tool_name == other.tool_name
            )

    def _tool_stub(
        name: str,
        description: str = "",
        parameters: Any = None,
    ) -> MagicMock:
        m = MagicMock()
        m.name = name
        m.description = description
        m.parameters = parameters or {}
        return m

    xai_chat.required_tool = _RequiredTool
    xai_chat.tool = _tool_stub


_extend_xai_stub_for_tools()


class TestXAIFormatTools(unittest.TestCase):
    """Tests for XAIChatModel._format_tools."""

    def setUp(self) -> None:
        self.model = _make_model()

    def test_no_tool_choice(self) -> None:
        """All tools are returned when tool_choice is None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertIsNotNone(fmt_tools)
        self.assertEqual(len(fmt_tools), 2)
        self.assertIsNone(fmt_choice)

    def test_auto_mode(self) -> None:
        """Auto mode passes tools through with choice 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertIsNotNone(fmt_tools)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """None mode passes tools through with choice 'none'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertIsNotNone(fmt_tools)
        self.assertEqual(fmt_choice, "none")

    def test_str_mode_force_call(self) -> None:
        """String mode forces a required_tool for the named function."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertIsNotNone(fmt_tools)
        self.assertEqual(fmt_choice.tool_name, "get_weather")

    def test_tools_filtered(self) -> None:
        """ToolChoice with tools list filters to matching function names."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0].name, "get_weather")
        self.assertEqual(fmt_choice, "auto")
