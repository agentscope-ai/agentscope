# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for MiniMaxChatModel with mocked API responses.

MiniMax exposes an OpenAI-compatible ``/v1/chat/completions`` endpoint, so
the mock structure mirrors the OpenAI chat tests. Coverage includes:

- Non-stream mode returns a single ChatResponse with is_last=True.
- Stream mode yields n delta ChatResponses (is_last=False) followed by
  1 final ChatResponse (is_last=True) with the full accumulated content.
- Tool calls are forwarded and parsed correctly in both modes.
- Custom ``base_url`` is honoured when constructing the OpenAI client.
"""

from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from utils import AnyString

from agentscope.message import (
    TextBlock,
    ToolCallBlock,
)
from agentscope.model import MiniMaxChatModel
from agentscope.credential import MiniMaxCredential

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(
    stream: bool = False,
    base_url: str = "https://api.minimax.cn/v1",
) -> Any:
    """Build a MiniMaxChatModel wired to a test base_url."""
    return MiniMaxChatModel(
        credential=MiniMaxCredential(
            api_key="test-key",
            base_url=base_url,
        ),
        model="minimax-m3",
        stream=stream,
        context_size=131_072,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    response_id: str = "resp-1",
) -> MagicMock:
    """Build a mock non-streaming ChatCompletion response."""
    msg = MagicMock()
    msg.content = text

    if tool_calls:
        tc_mocks = []
        for tc in tool_calls:
            m = MagicMock()
            m.id = tc["id"]
            m.function.name = tc["name"]
            m.function.arguments = tc["arguments"]
            tc_mocks.append(m)
        msg.tool_calls = tc_mocks
    else:
        msg.tool_calls = None

    choice = MagicMock()
    choice.message = msg

    resp = MagicMock()
    resp.id = response_id
    resp.choices = [choice]
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


def _make_stream_chunk(
    delta_text: str | None = None,
    tool_calls: list | None = None,
    response_id: str = "resp-1",
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
    else:
        chunk.usage = None

    if has_choices:
        delta = MagicMock()
        delta.content = delta_text
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


class TestMiniMaxChatNonStream(IsolatedAsyncioTestCase):
    """Tests for MiniMaxChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)
        # Inject a mock client so create() never hits the network.
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_text_response(self) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello from M3!"),
        )
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Hello from M3!",
                    ),
                ],
            ),
        )
        self.assertEqual(result.id, "resp-1")

    async def test_tool_call_response(self) -> None:
        """Non-stream tool calls are parsed into ToolCallBlocks."""
        tool_calls_payload = [
            {
                "id": "call_1",
                "name": "get_weather",
                "arguments": '{"city": "Shanghai"}',
            },
        ]
        mock_create = AsyncMock(
            return_value=_mock_completion(
                text=None,
                tool_calls=tool_calls_payload,
            ),
        )
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertTrue(result.is_last)
        self.assertEqual(len(result.content), 1)
        block = result.content[0]
        self.assertIsInstance(block, ToolCallBlock)
        self.assertEqual(block.id, "call_1")
        self.assertEqual(block.name, "get_weather")
        self.assertEqual(block.input, '{"city": "Shanghai"}')

    async def test_default_parameters_dont_force_temperature(self) -> None:
        """Default parameters leave temperature unset on the request."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="ok"),
        )
        self.mock_client.chat.completions.create = mock_create

        await self.model([])

        call_kwargs = mock_create.call_args.kwargs
        self.assertNotIn("temperature", call_kwargs)
        self.assertNotIn("max_tokens", call_kwargs)
        self.assertNotIn("top_p", call_kwargs)

    async def test_explicit_parameters_forwarded(self) -> None:
        """Explicit parameter overrides are forwarded to the API call."""
        model = MiniMaxChatModel(
            credential=MiniMaxCredential(api_key="test-key"),
            model="minimax-m3",
            stream=False,
            context_size=131_072,
            parameters=MiniMaxChatModel.Parameters(
                max_tokens=512,
                temperature=0.5,
                top_p=0.9,
            ),
        )
        model.client = MagicMock()
        mock_create = AsyncMock(
            return_value=_mock_completion(text="ok"),
        )
        model.client.chat.completions.create = mock_create

        await model([])

        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["max_tokens"], 512)
        self.assertEqual(call_kwargs["temperature"], 0.5)
        self.assertEqual(call_kwargs["top_p"], 0.9)


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestMiniMaxChatStream(IsolatedAsyncioTestCase):
    """Tests for MiniMaxChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_stream_text_response(self) -> None:
        """Stream yields n deltas (is_last=False) + 1 final."""
        chunks = [
            _make_stream_chunk(delta_text="Hello"),
            _make_stream_chunk(delta_text=" M3"),
            _make_stream_chunk(delta_text="!"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 7, "completion_tokens": 3},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        self.mock_client.chat.completions.create = mock_create

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
                            text=" M3",
                        ),
                    ],
                ),
                (
                    False,
                    [TextBlock.model_construct(id=A, created_at=A, text="!")],
                ),
                (
                    True,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Hello M3!",
                        ),
                    ],
                ),
            ],
        )
        self.assertEqual(responses[-1].id, "resp-1")

    async def test_stream_tool_call_response(self) -> None:
        """Stream tool_call deltas are aggregated into one final block."""
        chunks = [
            _make_stream_chunk(
                tool_calls=[
                    _make_tool_call_delta(
                        index=0,
                        tc_id="call_2",
                        name="search_docs",
                    ),
                ],
            ),
            _make_stream_chunk(
                tool_calls=[
                    _make_tool_call_delta(
                        index=0,
                        arguments='{"q": "MiniMax"}',
                    ),
                ],
            ),
            _make_stream_chunk(has_choices=False, usage=None),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        self.mock_client.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        # Last response should aggregate the full tool call.
        last = responses[-1]
        self.assertTrue(last.is_last)
        self.assertEqual(len(last.content), 1)
        block = last.content[0]
        self.assertIsInstance(block, ToolCallBlock)
        self.assertEqual(block.id, "call_2")
        self.assertEqual(block.name, "search_docs")
        self.assertEqual(block.input, '{"q": "MiniMax"}')


# ---------------------------------------------------------------------------
# Parameter + client tests
# ---------------------------------------------------------------------------


class TestMiniMaxChatParameters(unittest.TestCase):
    """Parameter validation tests."""

    def test_default_parameter_values(self) -> None:
        """All parameters are optional with sensible defaults."""
        params = MiniMaxChatModel.Parameters()
        self.assertIsNone(params.max_tokens)
        self.assertIsNone(params.temperature)
        self.assertIsNone(params.top_p)
        self.assertTrue(params.parallel_tool_calls)

    def test_parameter_constraints(self) -> None:
        """Out-of-range parameters are rejected by pydantic."""
        with self.assertRaises(ValueError):
            MiniMaxChatModel.Parameters(temperature=3.0)  # must be < 2
        with self.assertRaises(ValueError):
            MiniMaxChatModel.Parameters(top_p=2.0)  # must be <= 1
        with self.assertRaises(ValueError):
            MiniMaxChatModel.Parameters(max_tokens=0)  # must be > 0


class TestMiniMaxChatClient(unittest.TestCase):
    """Client construction tests."""

    def test_custom_base_url(self) -> None:
        """A custom base_url is forwarded to the openai.AsyncClient."""
        model = _make_model(
            stream=False,
            base_url="https://my-proxy.example.com/v1",
        )
        self.assertIn(
            "my-proxy.example.com",
            str(model.client.base_url),
        )

    def test_default_base_url(self) -> None:
        """The default base_url is the public MiniMax endpoint."""
        model = _make_model(stream=False)
        self.assertIn(
            "minimax.cn",
            str(model.client.base_url),
        )


# ---------------------------------------------------------------------------
# Tool formatting tests
# ---------------------------------------------------------------------------


class TestMiniMaxChatFormatTools(unittest.TestCase):
    """Tool schema conversion tests."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    def test_format_tools_wraps_in_function_type(self) -> None:
        """Tool schemas are wrapped in the OpenAI ``function`` envelope."""
        schemas = [
            {
                "function": {
                    "name": "search",
                    "description": "Search the web.",
                    "parameters": {"type": "object"},
                },
            },
        ]
        out = self.model._format_tools(schemas)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "function")
        self.assertEqual(out[0]["function"]["name"], "search")
        self.assertEqual(out[0]["function"]["description"], "Search the web.")
        self.assertEqual(out[0]["function"]["parameters"], {"type": "object"})

    def test_format_tools_handles_multiple_tools(self) -> None:
        """Multiple tool schemas produce one entry per schema."""
        schemas = [
            {"function": {"name": "a", "description": "A", "parameters": {}}},
            {"function": {"name": "b", "description": "B", "parameters": {}}},
        ]
        out = self.model._format_tools(schemas)
        self.assertEqual([o["function"]["name"] for o in out], ["a", "b"])


# ---------------------------------------------------------------------------
# Credential tests
# ---------------------------------------------------------------------------


class TestMiniMaxCredential(unittest.TestCase):
    """Credential class tests."""

    def test_credential_default_base_url(self) -> None:
        """MiniMaxCredential defaults to the public MiniMax endpoint."""
        cred = MiniMaxCredential(api_key="abc")
        self.assertEqual(cred.base_url, "https://api.minimax.cn/v1")
        self.assertEqual(cred.type, "MiniMax_credential")

    def test_credential_get_chat_model_class(self) -> None:
        """Credential.get_chat_model_class returns MiniMaxChatModel."""
        cred = MiniMaxCredential(api_key="abc")
        self.assertIs(cred.get_chat_model_class(), MiniMaxChatModel)


def test_model_name_mapping() -> None:
    """Ensure the AgentScope model id is normalized to the upstream id."""
    cred = MiniMaxCredential(api_key="test-key")
    # Lowercase AgentScope id is mapped to the upstream CamelCase id.
    m = MiniMaxChatModel(credential=cred, model="minimax-m3")
    assert m._model_name == "MiniMax-M3"

    # The upstream CamelCase id passes through unchanged.
    m2 = MiniMaxChatModel(credential=cred, model="MiniMax-M3")
    assert m2._model_name == "MiniMax-M3"

    # Unknown ids also pass through (so future models keep working).
    m3 = MiniMaxChatModel(credential=cred, model="minimax-future")
    assert m3._model_name == "minimax-future"


if __name__ == "__main__":
    unittest.main()
