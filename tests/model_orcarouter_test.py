# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OrcaRouterChatModel with mocked API responses."""
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import OrcaRouterChatModel
from agentscope.credential import OrcaRouterCredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return OrcaRouterChatModel(
        credential=OrcaRouterCredential(api_key="sk-orca-test"),
        model="orcarouter/auto",
        stream=stream,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    reasoning: Any = None,
    response_id: str = "or-1",
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
    response_id: str = "or-1",
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
# Credential / client wiring
# ---------------------------------------------------------------------------


class TestOrcaRouterCredential(unittest.TestCase):
    """Tests for OrcaRouterCredential defaults."""

    def test_default_base_url(self) -> None:
        cred = OrcaRouterCredential(api_key="sk-orca-test")
        self.assertEqual(cred.base_url, "https://api.orcarouter.ai/v1")

    def test_get_chat_model_class(self) -> None:
        self.assertIs(
            OrcaRouterCredential.get_chat_model_class(),
            OrcaRouterChatModel,
        )


class TestOrcaRouterClientWiring(IsolatedAsyncioTestCase):
    """Verify the OpenAI AsyncClient is created with OrcaRouter base_url and
    attribution headers."""

    @patch("openai.AsyncClient")
    async def test_client_built_with_attribution_headers(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        model = _make_model(stream=False)
        mock_create = AsyncMock(return_value=_mock_completion(text="ok"))
        mock_client_cls.return_value.chat.completions.create = mock_create

        await model([])

        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "sk-orca-test")
        self.assertEqual(kwargs["base_url"], "https://api.orcarouter.ai/v1")
        self.assertEqual(
            kwargs["default_headers"],
            {
                "HTTP-Referer": "https://www.agentscope.io/",
                "X-Title": "AgentScope",
            },
        )


# ---------------------------------------------------------------------------
# Non-streaming tests
# ---------------------------------------------------------------------------


class TestOrcaRouterNonStream(IsolatedAsyncioTestCase):
    """Tests for OrcaRouterChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("openai.AsyncClient")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello world!"),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello world!")]),
        )
        self.assertEqual(result.id, "or-1")

    @patch("openai.AsyncClient")
    async def test_tool_call_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        mock_create = AsyncMock(
            return_value=_mock_completion(
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "get_weather",
                        "arguments": '{"city":"Beijing"}',
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
                ],
            ),
        )

    @patch("openai.AsyncClient")
    async def test_thinking_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
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


class TestOrcaRouterStream(IsolatedAsyncioTestCase):
    """Tests for OrcaRouterChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("openai.AsyncClient")
    async def test_stream_text_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
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
                (
                    True,
                    [TextBlock.model_construct(id=A, text="Hello world!")],
                ),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_tool_calls(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        chunks = [
            _make_stream_chunk(
                tool_calls=[
                    _make_tool_call_delta(0, "call-1", "get_weather", '{"ci'),
                ],
            ),
            _make_stream_chunk(
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

        self.assertEqual(responses[-1].is_last, True)
        self.assertEqual(
            responses[-1].content,
            [
                ToolCallBlock(
                    id="call-1",
                    name="get_weather",
                    input='{"city":"BJ"}',
                ),
            ],
        )


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


class TestOrcaRouterParameters(unittest.TestCase):
    """Tests for OrcaRouterChatModel.Parameters."""

    def test_reasoning_effort_passed_to_kwargs(self) -> None:
        model = OrcaRouterChatModel(
            credential=OrcaRouterCredential(api_key="sk-orca-test"),
            model="openai/gpt-5",
            stream=False,
            parameters=OrcaRouterChatModel.Parameters(
                thinking_enable=True,
                reasoning_effort="high",
            ),
        )
        self.assertTrue(model.parameters.thinking_enable)
        self.assertEqual(model.parameters.reasoning_effort, "high")


# ---------------------------------------------------------------------------
# _format_tools
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
]


class TestOrcaRouterFormatTools(unittest.TestCase):
    """Tests for OrcaRouterChatModel._format_tools."""

    def setUp(self) -> None:
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "auto")

    def test_str_mode_force_call(self) -> None:
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(
            fmt_choice,
            {"type": "function", "function": {"name": "get_weather"}},
        )

    def test_no_tool_choice(self) -> None:
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertIsNone(fmt_choice)


if __name__ == "__main__":
    unittest.main()
