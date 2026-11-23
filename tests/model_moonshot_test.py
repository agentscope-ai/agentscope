# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for MoonshotChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import json
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, call

import httpx

from utils import AnyString

from agentscope.exception import StructuredOutputError
from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock, UserMsg
from agentscope.model import MoonshotChatModel, StructuredResponse
from agentscope.credential import MoonshotCredential
from agentscope.tool import ToolChoice

A = AnyString()
_K3_MODELS = ("kimi-k3", "kimi-k3.1-pro", "kimi-k3.1-pro-highspeed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return MoonshotChatModel(
        credential=MoonshotCredential(api_key="test"),
        model="kimi-k2-5",
        stream=stream,
        context_size=131_072,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    reasoning: Any = None,
    response_id: str = "kimi-1",
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
    resp.usage.prompt_tokens_details.cached_tokens = 0
    return resp


def _make_stream_chunk(
    delta_text: str | None = None,
    delta_reasoning: str | None = None,
    tool_calls: list | None = None,
    response_id: str = "kimi-1",
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
        chunk.usage.prompt_tokens_details.cached_tokens = usage.get(
            "cached_tokens",
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
    tc = MagicMock()
    tc.index = index
    tc.id = tc_id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


class _MockAsyncStream:
    """Mock async stream (context manager + async iterator)."""

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


class TestMoonshotNonStream(IsolatedAsyncioTestCase):
    """Tests for MoonshotChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_text_response(self) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [TextBlock.model_construct(id=A, created_at=A, text="Hello!")],
            ),
        )
        self.assertEqual(result.id, "kimi-1")

    async def test_usage_cache_input_tokens_from_nested_details(self) -> None:
        """Read cached tokens from nested usage details."""
        resp = _mock_completion(text="Hello!")
        resp.usage.prompt_tokens = 1000
        resp.usage.completion_tokens = 20
        resp.usage.prompt_tokens_details.cached_tokens = 832
        mock_create = AsyncMock(return_value=resp)
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            dict(result.usage),
            {
                "input_tokens": 1000,
                "output_tokens": 20,
                "time": result.usage.time,
                "cache_creation_input_tokens": 0,
                "cache_input_tokens": 832,
                "type": "chat",
                "metadata": None,
            },
        )

    async def test_usage_cache_input_tokens_absent_details(self) -> None:
        """Default cached tokens to zero without usage details."""
        resp = _mock_completion(text="Hello!")
        del resp.usage.prompt_tokens_details
        mock_create = AsyncMock(return_value=resp)
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            dict(result.usage),
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "time": result.usage.time,
                "cache_creation_input_tokens": 0,
                "cache_input_tokens": 0,
                "type": "chat",
                "metadata": None,
            },
        )

    async def test_extra_body_is_not_mutated(self) -> None:
        """Default parameters leave the caller's extra_body intact — no
        ``thinking`` option is injected and the caller's dict is untouched."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        self.mock_client.chat.completions.create = mock_create
        extra_body = {"custom": "value", "thinking": {"budget_tokens": 1024}}

        await self.model([], extra_body=extra_body)

        self.assertDictEqual(
            extra_body,
            {"custom": "value", "thinking": {"budget_tokens": 1024}},
        )
        self.assertDictEqual(
            mock_create.await_args.kwargs["extra_body"],
            {"custom": "value", "thinking": {"budget_tokens": 1024}},
        )

    async def test_thinking_omitted_by_default(self) -> None:
        """Default parameters send no ``thinking`` option so the model's
        default behavior applies."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        self.mock_client.chat.completions.create = mock_create

        await self.model([])

        extra_body = mock_create.await_args.kwargs.get("extra_body") or {}
        self.assertNotIn("thinking", extra_body)

    async def test_thinking_disabled_forwarded(self) -> None:
        """``thinking_enable=False`` sends ``thinking.type: disabled``."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        self.mock_client.chat.completions.create = mock_create
        self.model.parameters = MoonshotChatModel.Parameters(
            thinking_enable=False,
        )

        await self.model([])

        extra_body = mock_create.await_args.kwargs["extra_body"]
        self.assertEqual(
            extra_body,
            {"thinking": {"type": "disabled"}},
        )

    async def test_thinking_enabled_forwarded(self) -> None:
        """``thinking_enable=True`` sends ``thinking.type: enabled``."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        self.mock_client.chat.completions.create = mock_create
        self.model.parameters = MoonshotChatModel.Parameters(
            thinking_enable=True,
        )

        await self.model([])

        extra_body = mock_create.await_args.kwargs["extra_body"]
        self.assertEqual(
            extra_body,
            {"thinking": {"type": "enabled"}},
        )

    async def test_tool_call_response(
        self,
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
        self.mock_client.chat.completions.create = mock_create

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
                        input='{"city":"Shanghai"}',
                    ),
                ],
            ),
        )

    async def test_thinking_response(
        self,
    ) -> None:
        """Non-stream response with reasoning creates ThinkingBlock."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                text="42",
                reasoning="Step by step...",
            ),
        )
        self.mock_client.chat.completions.create = mock_create

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
                    ),
                    TextBlock.model_construct(id=A, created_at=A, text="42"),
                ],
            ),
        )


class TestKimiModelParameters(unittest.TestCase):
    """Tests for MoonshotChatModel.Parameters."""

    def test_thinking_enable_stored_on_model(self) -> None:
        """thinking_enable is accessible through model.parameters."""
        model = MoonshotChatModel(
            credential=MoonshotCredential(api_key="test"),
            model="kimi-k2-5",
            stream=False,
            context_size=131_072,
            parameters=MoonshotChatModel.Parameters(thinking_enable=True),
        )
        self.assertTrue(model.parameters.thinking_enable)


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestMoonshotStream(IsolatedAsyncioTestCase):
    """Tests for MoonshotChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_stream_text(self) -> None:
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
        self.mock_client.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [TextBlock.model_construct(id=A, created_at=A, text="Hi")],
                ),
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text=" there",
                        ),
                    ],
                ),
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
            ],
        )

    async def test_stream_usage_cache_input_tokens_from_nested_details(
        self,
    ) -> None:
        """Read cached tokens from a streaming usage chunk."""
        chunks = [
            _make_stream_chunk(delta_text="Hi"),
            _make_stream_chunk(
                has_choices=False,
                usage={
                    "prompt_tokens": 1000,
                    "completion_tokens": 20,
                    "cached_tokens": 832,
                },
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        self.mock_client.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertEqual(
            dict(responses[-1].usage),
            {
                "input_tokens": 1000,
                "output_tokens": 20,
                "time": responses[-1].usage.time,
                "cache_creation_input_tokens": 0,
                "cache_input_tokens": 832,
                "type": "chat",
                "metadata": None,
            },
        )

    async def test_stream_thinking_then_text(
        self,
    ) -> None:
        """Moonshot yields thinking chunks separately before text."""
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
        self.mock_client.chat.completions.create = mock_create

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
                            thinking="Let me",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking=" think",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Result",
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="Let me think",
                        ),
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Result",
                        ),
                    ],
                ),
            ],
        )

    async def test_stream_tool_calls(
        self,
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
        self.mock_client.chat.completions.create = mock_create

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


class TestMoonshotFormatTools(unittest.TestCase):
    """Tests for MoonshotChatModel._format_tools."""

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


class MoonshotK3Test(IsolatedAsyncioTestCase):
    """Verify the shared request contract for the K3 series."""

    async def test_request_parameters(self) -> None:
        """Translate budgets, preserve overrides, and omit fixed settings."""
        cases: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = [
            ({}, {}, {}),
            ({"max_tokens": 1024}, {}, {"max_completion_tokens": 1024}),
            (
                {"max_tokens": 1024},
                {"max_tokens": 2048},
                {"max_completion_tokens": 2048},
            ),
            (
                {"max_tokens": 1024},
                {"max_completion_tokens": 4096},
                {"max_completion_tokens": 4096},
            ),
            (
                {"max_tokens": 1024},
                {"max_tokens": 2048, "max_completion_tokens": 4096},
                {"max_completion_tokens": 4096},
            ),
            (
                {},
                {"max_tokens": 1048576},
                {"max_completion_tokens": 1048576},
            ),
            (
                {
                    "temperature": 0.2,
                    "top_p": 0.4,
                    "thinking_enable": False,
                },
                {},
                {},
            ),
            ({"thinking_enable": True}, {}, {}),
            (
                {},
                {
                    "temperature": 1.0,
                    "top_p": 0.95,
                    "n": 1,
                    "presence_penalty": 0,
                    "frequency_penalty": 0,
                },
                {},
            ),
            (
                {},
                {
                    "temperature": 0.2,
                    "top_p": 0.4,
                    "n": 2,
                    "presence_penalty": 1,
                    "frequency_penalty": 1,
                    "thinking": {"type": "disabled"},
                },
                {},
            ),
            (
                {},
                {
                    "extra_body": {
                        "temperature": 0.2,
                        "top_p": 0.4,
                        "n": 2,
                        "presence_penalty": 1,
                        "frequency_penalty": 1,
                        "thinking": {"type": "disabled"},
                        "prompt_cache_options": {"ttl": "1h"},
                    },
                },
                {"extra_body": {"prompt_cache_options": {"ttl": "1h"}}},
            ),
        ]
        for effort in ("low", "high", "max"):
            cases.extend(
                [
                    (
                        {"reasoning_effort": effort},
                        {},
                        {"reasoning_effort": effort},
                    ),
                    (
                        {"reasoning_effort": "max"},
                        {"reasoning_effort": effort},
                        {"reasoning_effort": effort},
                    ),
                ],
            )
        for name in _K3_MODELS:
            model = _make_model()
            model.model = name
            create = AsyncMock(return_value=_mock_completion(text="OK"))
            model.client.chat.completions.create = create
            for parameters, kwargs, expected in cases:
                with self.subTest(
                    model=name,
                    parameters=parameters,
                    kwargs=kwargs,
                ):
                    model.parameters = MoonshotChatModel.Parameters(
                        **parameters,
                    )
                    original = deepcopy(kwargs)
                    await model([], **kwargs)
                    self.assertDictEqual(
                        create.call_args.kwargs,
                        {
                            "model": name,
                            "messages": [],
                            "stream": False,
                            **expected,
                        },
                    )
                    self.assertDictEqual(kwargs, original)
            await model.client.close()

    async def test_extra_body_budget_http_request(self) -> None:
        """Normalize budgets before the SDK merges the final HTTP body."""
        cases: list[
            tuple[int | None, dict[str, int], dict[str, int], dict[str, int]]
        ] = [
            (None, {}, {}, {}),
            (None, {}, {"max_tokens": 2048}, {"max_completion_tokens": 2048}),
            (1024, {}, {}, {"max_completion_tokens": 1024}),
            (1024, {}, {"max_tokens": 2048}, {"max_completion_tokens": 2048}),
            (
                1024,
                {},
                {"max_completion_tokens": 2048},
                {"max_completion_tokens": 2048},
            ),
            (
                1024,
                {"max_tokens": 4096},
                {"max_tokens": 2048},
                {"max_completion_tokens": 2048},
            ),
            (
                1024,
                {"max_completion_tokens": 4096},
                {"max_tokens": 2048},
                {"max_completion_tokens": 2048},
            ),
            (
                1024,
                {"max_tokens": 4096, "max_completion_tokens": 8192},
                {"max_tokens": 2048, "max_completion_tokens": 3072},
                {"max_completion_tokens": 3072},
            ),
        ]
        bodies: list[dict] = []

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if body["stream"]:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    text="data: [DONE]\n\n",
                )
            return httpx.Response(
                200,
                json={
                    "id": "kimi-1",
                    "created": 0,
                    "model": body["model"],
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "OK"},
                        },
                    ],
                },
            )

        for name in _K3_MODELS:
            for stream in (False, True):
                model = MoonshotChatModel(
                    credential=MoonshotCredential(api_key="test"),
                    model=name,
                    stream=stream,
                    client_kwargs={
                        "http_client": httpx.AsyncClient(
                            transport=httpx.MockTransport(handle),
                        ),
                    },
                )
                self.addAsyncCleanup(model.client.close)
                for budget, kwargs, extra_budget, expected in cases:
                    with self.subTest(
                        model=name,
                        stream=stream,
                        budget=budget,
                        kwargs=kwargs,
                        extra_budget=extra_budget,
                    ):
                        model.parameters = MoonshotChatModel.Parameters(
                            max_tokens=budget,
                        )
                        extra_body = {
                            **extra_budget,
                            "prompt_cache_options": {"ttl": "1h"},
                            "temperature": 0.2,
                            "thinking": {"type": "disabled"},
                        }
                        original = deepcopy(extra_body)
                        bodies.clear()
                        response = await model(
                            [],
                            extra_body=extra_body,
                            **kwargs,
                        )
                        if stream:
                            async for _ in response:
                                pass
                        self.assertEqual(
                            bodies,
                            [
                                {
                                    "model": name,
                                    "messages": [],
                                    "stream": stream,
                                    "prompt_cache_options": {"ttl": "1h"},
                                    **expected,
                                    **(
                                        {
                                            "stream_options": {
                                                "include_usage": True,
                                            },
                                        }
                                        if stream
                                        else {}
                                    ),
                                },
                            ],
                        )
                        self.assertEqual(extra_body, original)

    async def test_legacy_request_parameters(self) -> None:
        """Existing models keep their sampling and thinking behavior."""
        for name in (
            "kimi-k2.6",
            "kimi-k2.7-code",
            "kimi-k2.7-code-highspeed",
            "moonshot-v1-8k",
        ):
            with self.subTest(model=name):
                model = _make_model()
                model.model = name
                model.parameters = MoonshotChatModel.Parameters(
                    max_tokens=1024,
                    reasoning_effort="high",
                    temperature=0.2,
                    top_p=0.4,
                )
                create = AsyncMock(return_value=_mock_completion(text="OK"))
                model.client.chat.completions.create = create
                await model([])
                expected = {
                    "model": name,
                    "messages": [],
                    "stream": False,
                    "temperature": 0.2,
                    "top_p": 0.4,
                    "max_tokens": 1024,
                }
                self.assertDictEqual(create.call_args.kwargs, expected)
                self.assertDictEqual(
                    model._get_disable_thinking_kwargs(),
                    {
                        "extra_body": {
                            "thinking": {"type": "disabled"},
                        },
                    },
                )
                await model.client.close()

    async def test_structured_output_skips_disable_thinking(self) -> None:
        """The real fallback ladder must not attempt to turn thinking off."""
        for name in _K3_MODELS:
            with self.subTest(model=name):
                model = _make_model()
                model.model = name
                messages = [UserMsg("user", "Return an answer")]
                schema = {"type": "object"}
                attempt = AsyncMock(
                    side_effect=[
                        StructuredOutputError("try auto"),
                        StructuredOutputError("try unforced"),
                        StructuredResponse(content={"answer": 42}),
                    ],
                )
                model._call_api_with_structured_output = attempt
                result = await model.generate_structured_output(
                    messages,
                    schema,
                )
                self.assertDictEqual(
                    asdict(result),
                    {
                        "content": {"answer": 42},
                        "usage": None,
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "type": "structured_response",
                        "metadata": {},
                        "finished_reason": "completed",
                    },
                )
                self.assertListEqual(
                    attempt.call_args_list,
                    [
                        call(
                            name,
                            messages=messages,
                            structured_model=schema,
                            tool_choice=ToolChoice(
                                mode="generate_structured_output",
                            ),
                        ),
                        call(
                            name,
                            messages=messages,
                            structured_model=schema,
                            tool_choice=ToolChoice(mode="auto"),
                        ),
                        call(
                            name,
                            messages=messages,
                            structured_model=schema,
                            tool_choice=None,
                        ),
                    ],
                )
                await model.client.close()

    async def test_named_tool_request(self) -> None:
        """Force only the selected tool without mutating caller schemas."""
        for name in _K3_MODELS:
            with self.subTest(model=name):
                model = _make_model()
                self.addAsyncCleanup(model.client.close)
                model.model = name
                create = AsyncMock(return_value=_mock_completion(text="OK"))
                model.client.chat.completions.create = create
                tools = deepcopy(_FT_TOOLS)
                await model(
                    [],
                    tools=tools,
                    tool_choice=ToolChoice(mode="get_weather"),
                )
                self.assertDictEqual(
                    create.call_args.kwargs,
                    {
                        "model": name,
                        "messages": [],
                        "stream": False,
                        "tools": [_FT_TOOLS[0]],
                        "tool_choice": "required",
                    },
                )
                self.assertEqual(tools, _FT_TOOLS)

    async def test_structured_output_uses_required(self) -> None:
        """Generate structured output on the first API request."""
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
        }
        for name in _K3_MODELS:
            with self.subTest(model=name):
                model = _make_model()
                self.addAsyncCleanup(model.client.close)
                model.model = name
                create = AsyncMock(
                    return_value=_mock_completion(
                        tool_calls=[
                            {
                                "id": "call-1",
                                "name": "generate_structured_output",
                                "arguments": '{"answer": 42}',
                            },
                        ],
                    ),
                )
                model.client.chat.completions.create = create
                result = await model.generate_structured_output(
                    [UserMsg("user", "Return an answer")],
                    schema,
                )
                self.assertEqual(result.content, {"answer": 42})
                create.assert_awaited_once()
                self.assertEqual(
                    (
                        create.call_args.kwargs["tool_choice"],
                        create.call_args.kwargs["tools"],
                    ),
                    (
                        "required",
                        [
                            {
                                "type": "function",
                                "function": {
                                    "name": "generate_structured_output",
                                    "description": "Call this function to "
                                    "generate structured output required "
                                    "by the user.",
                                    "parameters": schema,
                                },
                            },
                        ],
                    ),
                )

    def test_model_cards(self) -> None:
        """All K3 models expose consistent capabilities and parameters."""
        cards = {card.name: card for card in MoonshotChatModel.list_models()}
        labels = {
            "kimi-k3": "Kimi K3",
            "kimi-k3.1-pro": "Kimi K3.1 Pro",
            "kimi-k3.1-pro-highspeed": "Kimi K3.1 Pro Highspeed",
        }
        release_dates = {
            "kimi-k3": date(2026, 7, 16),
            "kimi-k3.1-pro": date(2026, 10, 23),
            "kimi-k3.1-pro-highspeed": date(2026, 10, 23),
        }
        for name in _K3_MODELS:
            with self.subTest(model=name):
                self.assertIn(name, cards)
                self.assertDictEqual(
                    cards[name].model_dump(),
                    {
                        "type": "chat_model",
                        "model_class": "moonshot_chat",
                        "name": name,
                        "label": labels[name],
                        "status": "active",
                        "deprecated_at": None,
                        "release_date": release_dates[name],
                        "input_types": [
                            "text/plain",
                            "application/x-thinking",
                            "image/jpeg",
                            "image/png",
                            "image/gif",
                            "image/webp",
                            "video/mp4",
                            "video/mpeg",
                            "video/quicktime",
                            "video/avi",
                            "video/webm",
                        ],
                        "output_types": [
                            "text/plain",
                            "application/x-thinking",
                        ],
                        "context_size": 1048576,
                        "output_size": 1048576,
                        "parameters_overrides": {
                            "max_tokens": {"maximum": 1048576},
                            "thinking_enable": {"hidden": True},
                            "temperature": {"hidden": True},
                            "top_p": {"hidden": True},
                        },
                        "parameter_schema": {
                            "type": "object",
                            "required": [],
                            "properties": {
                                "max_tokens": {
                                    "anyOf": [
                                        {
                                            "exclusiveMinimum": 0,
                                            "type": "integer",
                                        },
                                        {"type": "null"},
                                    ],
                                    "default": None,
                                    "title": "Max Tokens",
                                    "description": AnyString(),
                                    "maximum": 1048576,
                                },
                                "reasoning_effort": {
                                    "anyOf": [
                                        {
                                            "enum": ["low", "high", "max"],
                                            "type": "string",
                                        },
                                        {"type": "null"},
                                    ],
                                    "default": None,
                                    "title": "Reasoning Effort",
                                    "description": AnyString(),
                                },
                            },
                        },
                    },
                )
