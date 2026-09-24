# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the MiniMax Anthropic-compatible model."""

import json
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from utils import AnyString

from agentscope.credential import MiniMaxCredential
from agentscope.message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.model import AnthropicChatModel, MiniMaxChatModel
from agentscope.tool import ToolChoice

A = AnyString()


def _make_model(stream: bool = False) -> MiniMaxChatModel:
    """Build a MiniMax model for tests."""
    return MiniMaxChatModel(
        credential=MiniMaxCredential(api_key="test"),
        model="MiniMax-M3",
        stream=stream,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    thinking: Any = None,
    response_id: str = "msg-1",
) -> MagicMock:
    """Build a mock Anthropic-compatible response."""
    blocks = []
    if thinking:
        block = MagicMock()
        block.type = "thinking"
        block.thinking = thinking
        block.signature = "sig123"
        blocks.append(block)
    if text:
        block = MagicMock()
        block.type = "text"
        block.text = text
        blocks.append(block)
    if tool_calls:
        for tool_call in tool_calls:
            block = MagicMock()
            block.type = "tool_use"
            block.id = tool_call["id"]
            block.name = tool_call["name"]
            block.input = tool_call["input"]
            blocks.append(block)

    response = MagicMock()
    response.id = response_id
    response.content = blocks
    response.usage = MagicMock()
    response.usage.input_tokens = 10
    response.usage.output_tokens = 5
    response.usage.cache_creation_input_tokens = 0
    response.usage.cache_read_input_tokens = 0
    return response


def _make_event(event_type: str, **kwargs: Any) -> MagicMock:
    """Build a mock Anthropic-compatible streaming event."""
    event = MagicMock()
    event.type = event_type
    for key, value in kwargs.items():
        setattr(event, key, value)
    return event


class _MockAsyncEventStream:
    """Mock an Anthropic asynchronous event stream."""

    def __init__(self, events: list) -> None:
        self._events = events
        self._index = 0

    async def __aenter__(self) -> "_MockAsyncEventStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def __aiter__(self) -> "_MockAsyncEventStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event


class TestMiniMaxNonStream(IsolatedAsyncioTestCase):
    """Tests for non-streaming MiniMax responses."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_text_response(self) -> None:
        """A text response is converted to a TextBlock."""
        self.mock_client.messages.create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [TextBlock.model_construct(id=A, created_at=A, text="Hello!")],
            ),
        )
        self.assertEqual(result.id, "msg-1")

    async def test_tool_call_response(self) -> None:
        """A tool-use response is converted to a ToolCallBlock."""
        self.mock_client.messages.create = AsyncMock(
            return_value=_mock_completion(
                tool_calls=[
                    {
                        "id": "toolu_1",
                        "name": "get_weather",
                        "input": {"city": "Shanghai"},
                    },
                ],
            ),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock.model_construct(
                        id="toolu_1",
                        name="get_weather",
                        input=json.dumps({"city": "Shanghai"}),
                        created_at=A,
                    ),
                ],
            ),
        )

    async def test_thinking_response(self) -> None:
        """A signed thinking block is preserved in the response."""
        self.mock_client.messages.create = AsyncMock(
            return_value=_mock_completion(
                thinking="Step by step...",
                text="42",
            ),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        created_at=A,
                        thinking="Step by step...",
                        signature="sig123",
                    ),
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="42",
                    ),
                ],
            ),
        )

    async def test_adaptive_thinking_request(self) -> None:
        """Enabling thinking sends MiniMax's adaptive configuration."""
        self.model.parameters.thinking_enable = True
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        self.mock_client.messages.create = mock_create

        await self.model([])

        self.assertEqual(
            mock_create.call_args.kwargs,
            {
                "model": "MiniMax-M3",
                "max_tokens": 8192,
                "stream": False,
                "thinking": {"type": "adaptive"},
                "messages": [],
            },
        )

    async def test_complete_tool_turn_is_replayed(self) -> None:
        """Thinking and tool-use blocks are replayed in the next request."""
        self.model.parameters.thinking_enable = True
        mock_create = AsyncMock(
            side_effect=[
                _mock_completion(
                    thinking="I should check the weather.",
                    tool_calls=[
                        {
                            "id": "toolu_1",
                            "name": "get_weather",
                            "input": {"city": "Shanghai"},
                        },
                    ],
                ),
                _mock_completion(text="It is sunny.", response_id="msg-2"),
            ],
        )
        self.mock_client.messages.create = mock_create
        user_msg = Msg(
            name="user",
            role="user",
            content=[TextBlock(text="How is the weather?")],
        )

        first_response = await self.model([user_msg])
        assistant_msg = Msg(
            name="assistant",
            role="assistant",
            content=first_response.content,
        )
        tool_result_msg = Msg(
            name="tool",
            role="assistant",
            content=[
                ToolResultBlock(
                    id="toolu_1",
                    name="get_weather",
                    output="Sunny, 25 C",
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )
        await self.model([user_msg, assistant_msg, tool_result_msg])

        self.assertEqual(
            mock_create.await_args_list[1].kwargs["messages"],
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "How is the weather?"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "I should check the weather.",
                            "signature": "sig123",
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "get_weather",
                            "input": {"city": "Shanghai"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [
                                {"type": "text", "text": "Sunny, 25 C"},
                            ],
                        },
                    ],
                },
            ],
        )


class TestMiniMaxModelParameters(unittest.TestCase):
    """Tests for MiniMax model configuration."""

    def test_parameter_schema(self) -> None:
        """Only parameters supported by MiniMax are exposed."""
        params = MiniMaxChatModel.Parameters()

        self.assertEqual(
            params.model_dump(),
            {"max_tokens": None, "thinking_enable": False},
        )

    def test_inherits_anthropic_model(self) -> None:
        """MiniMax reuses the Anthropic model implementation."""
        self.assertIsInstance(_make_model(), AnthropicChatModel)

    def test_default_context_size(self) -> None:
        """MiniMax-M3 defaults to its documented 1M context window."""
        self.assertEqual(_make_model().context_size, 1_000_000)

    def test_default_base_url(self) -> None:
        """The credential uses MiniMax's Anthropic-compatible endpoint."""
        credential = MiniMaxCredential(api_key="test")
        self.assertEqual(
            credential.base_url,
            "https://api.minimax.io/anthropic",
        )


class TestMiniMaxStream(IsolatedAsyncioTestCase):
    """Tests for streaming MiniMax responses."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_stream_text(self) -> None:
        """Text deltas are accumulated into the final response."""
        message = MagicMock()
        message.id = "msg-1"
        message.usage = MagicMock()
        message.usage.input_tokens = 10
        message.usage.output_tokens = 0
        message.usage.cache_creation_input_tokens = 0
        message.usage.cache_read_input_tokens = 0
        text_start = MagicMock()
        text_start.type = "text"
        delta_one = MagicMock(type="text_delta", text="Hi")
        delta_two = MagicMock(type="text_delta", text=" there")
        events = [
            _make_event("message_start", message=message),
            _make_event(
                "content_block_start",
                index=0,
                content_block=text_start,
            ),
            _make_event("content_block_delta", index=0, delta=delta_one),
            _make_event("content_block_delta", index=0, delta=delta_two),
        ]
        self.mock_client.messages.create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )

        responses = [response async for response in await self.model([])]

        self.assertEqual(
            (responses[-1].is_last, responses[-1].content),
            (
                True,
                [
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Hi there",
                    ),
                ],
            ),
        )

    async def test_stream_thinking_and_tool_call(self) -> None:
        """Streaming preserves signed thinking before the tool call."""
        message = MagicMock()
        message.id = "msg-2"
        message.usage = MagicMock()
        message.usage.input_tokens = 10
        message.usage.output_tokens = 0
        message.usage.cache_creation_input_tokens = 0
        message.usage.cache_read_input_tokens = 0
        thinking_start = MagicMock(type="thinking")
        thinking_delta = MagicMock(
            type="thinking_delta",
            thinking="I should search.",
        )
        signature_delta = MagicMock(
            type="signature_delta",
            signature="sig_abc",
        )
        tool_start = MagicMock()
        tool_start.type = "tool_use"
        tool_start.id = "toolu_1"
        tool_start.name = "search"
        json_delta = MagicMock(
            type="input_json_delta",
            partial_json='{"q":"test"}',
        )
        events = [
            _make_event("message_start", message=message),
            _make_event(
                "content_block_start",
                index=0,
                content_block=thinking_start,
            ),
            _make_event(
                "content_block_delta",
                index=0,
                delta=thinking_delta,
            ),
            _make_event(
                "content_block_delta",
                index=0,
                delta=signature_delta,
            ),
            _make_event(
                "content_block_start",
                index=1,
                content_block=tool_start,
            ),
            _make_event(
                "content_block_delta",
                index=1,
                delta=json_delta,
            ),
        ]
        self.mock_client.messages.create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )

        responses = [response async for response in await self.model([])]

        self.assertEqual(
            (responses[-1].is_last, responses[-1].content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        created_at=A,
                        thinking="I should search.",
                        signature="sig_abc",
                    ),
                    ToolCallBlock.model_construct(
                        id="toolu_1",
                        name="search",
                        input='{"q":"test"}',
                        created_at=A,
                    ),
                ],
            ),
        )


_TOOLS = [
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

_ANTHROPIC_TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the weather",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "get_time",
        "description": "Get the time",
        "input_schema": {
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
            "required": ["timezone"],
        },
    },
]


class TestMiniMaxFormatTools(unittest.TestCase):
    """Tests for MiniMax tool formatting."""

    def setUp(self) -> None:
        self.model = _make_model()

    def test_literal_modes(self) -> None:
        """Literal tool modes map to the Anthropic format."""
        expected = {
            "auto": {"type": "auto"},
            "none": {"type": "none"},
            "required": {"type": "any"},
        }
        for mode, expected_choice in expected.items():
            with self.subTest(mode=mode):
                tools, choice = self.model._format_tools(
                    _TOOLS,
                    ToolChoice(mode=mode),
                )
                self.assertEqual(
                    (tools, choice),
                    (_ANTHROPIC_TOOLS, expected_choice),
                )

    def test_specific_tool(self) -> None:
        """A named mode forces that tool."""
        tools, choice = self.model._format_tools(
            _TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(
            (tools, choice),
            (_ANTHROPIC_TOOLS, {"type": "tool", "name": "get_weather"}),
        )

    def test_tool_filter(self) -> None:
        """The allowed tool list filters schemas."""
        tools, choice = self.model._format_tools(
            _TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(
            (tools, choice),
            ([_ANTHROPIC_TOOLS[0]], {"type": "auto"}),
        )

    def test_no_tool_choice(self) -> None:
        """Missing tool choice leaves selection unspecified."""
        tools, choice = self.model._format_tools(_TOOLS, None)
        self.assertEqual((tools, choice), (_ANTHROPIC_TOOLS, None))


class TestMiniMaxModelListing(unittest.TestCase):
    """Tests for MiniMax model card discovery."""

    def test_model_cards(self) -> None:
        """The expected MiniMax model cards are available."""
        cards = MiniMaxChatModel.list_models()
        cards_by_name = {card.name: card for card in cards}

        self.assertEqual(
            set(cards_by_name),
            {"MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed"},
        )
        self.assertEqual(cards_by_name["MiniMax-M3"].status, "active")
        self.assertEqual(cards_by_name["MiniMax-M3"].context_size, 1_000_000)
        self.assertTrue(
            any(
                media_type.startswith("image/")
                for media_type in cards_by_name["MiniMax-M3"].input_types
            ),
        )
        self.assertTrue(
            any(
                media_type.startswith("video/")
                for media_type in cards_by_name["MiniMax-M3"].input_types
            ),
        )
        for model_name in ("MiniMax-M2.7", "MiniMax-M2.7-highspeed"):
            self.assertFalse(
                any(
                    media_type.startswith(("image/", "video/"))
                    for media_type in cards_by_name[model_name].input_types
                ),
            )

    def test_credential_lists_models(self) -> None:
        """The credential delegates model listing to MiniMaxChatModel."""
        names = {card.name for card in MiniMaxCredential.list_models()}
        self.assertIn("MiniMax-M3", names)


if __name__ == "__main__":
    unittest.main()
