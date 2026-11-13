# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for GeminiChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
Gemini uses google.genai client with async iterator streaming.
"""
import base64
import copy
import json
import os
from types import SimpleNamespace
from typing import Any, Annotated, Callable, Literal
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import httpx2
from google.genai.types import HttpOptions, ThinkingConfig
from pydantic import BaseModel, Field

from utils import AnyString

from agentscope.formatter import GeminiChatFormatter
from agentscope.message import (
    AssistantMsg,
    Msg,
    UserMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ThinkingBlock,
)
from agentscope.message._base import Usage
from agentscope.model import GeminiChatModel
from agentscope.model._gemini._model import _sanitize_schema_for_gemini
from agentscope._utils._common import _flatten_json_schema
from agentscope.credential import GeminiCredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return GeminiChatModel(
        credential=GeminiCredential(api_key="test"),
        model="gemini-2.5-flash",
        stream=stream,
        context_size=1_048_576,
    )


def _make_part(
    text: str | None = None,
    thought: bool = False,
    function_call: dict | None = None,
    thought_signature: Any = None,
) -> MagicMock:
    """Build a mock Gemini Part."""
    part = MagicMock()
    part.text = text
    part.thought = thought
    part.thought_signature = thought_signature
    if function_call:
        part.function_call = MagicMock()
        part.function_call.name = function_call["name"]
        part.function_call.args = function_call.get("args", {})
        part.function_call.id = function_call.get("id", "call-1")
    else:
        part.function_call = None
    return part


def _mock_completion(
    parts: list,
    response_id: str = "resp-gem-1",
) -> MagicMock:
    """Build a mock non-streaming Gemini response."""
    resp = MagicMock()
    resp.response_id = response_id
    resp.candidates = [MagicMock()]
    resp.candidates[0].content = MagicMock()
    resp.candidates[0].content.parts = parts
    resp.usage_metadata = SimpleNamespace(
        prompt_token_count=10,
        candidates_token_count=5,
        tool_use_prompt_token_count=0,
        thoughts_token_count=0,
        total_token_count=15,
        cached_content_token_count=0,
    )
    return resp


def _make_stream_chunk(
    parts: list,
    response_id: str = "resp-gem-1",
) -> MagicMock:
    """Build a single mock streaming chunk."""
    chunk = MagicMock()
    chunk.response_id = response_id
    chunk.candidates = [MagicMock()]
    chunk.candidates[0].content = MagicMock()
    chunk.candidates[0].content.parts = parts
    chunk.usage_metadata = SimpleNamespace(
        prompt_token_count=10,
        candidates_token_count=5,
        tool_use_prompt_token_count=0,
        thoughts_token_count=0,
        total_token_count=15,
        cached_content_token_count=0,
    )
    return chunk


class _MockAsyncStream:
    """Mock async iterator for Gemini stream."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self._index = 0

    def __aiter__(self) -> "_MockAsyncStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


# ---------------------------------------------------------------------------
# Gemini 4 configuration tests
# ---------------------------------------------------------------------------


class TestGemini4Argon(IsolatedAsyncioTestCase):
    """Verify Gemini 4 requests and configuration without network access."""

    model_name = "gemini-4-argon"
    model_label = "Gemini 4 Argon"
    thinking_levels = ("low", "medium", "high")
    default_thinking_level = "medium"

    def setUp(self) -> None:
        with patch("google.genai.Client"):
            self.model = GeminiChatModel(
                credential=GeminiCredential(api_key="test"),
                model=self.model_name,
                stream=False,
            )

    async def test_request_parameters(self) -> None:
        """Check defaults, levels, summaries, and per-call overrides."""
        cases: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = [
            ({}, {}, {}),
            (
                {"thinking_enable": False},
                {},
                {"thinking_config": {"include_thoughts": False}},
            ),
        ]
        for level in self.thinking_levels:
            cases.append(
                (
                    {"thinking_level": level, "thinking_enable": True},
                    {},
                    {
                        "thinking_config": {
                            "thinking_level": level.upper(),
                            "include_thoughts": True,
                        },
                    },
                ),
            )
        cases.append(
            (
                {"thinking_budget": 1024, "max_tokens": 100_000},
                {
                    "thinking_config": ThinkingConfig(thinking_level="LOW"),
                    "max_output_tokens": 65_536,
                    "temperature": 0.2,
                    "top_p": 0.3,
                    "top_k": 20,
                },
                {
                    "thinking_config": {"thinking_level": "LOW"},
                    "max_output_tokens": 65_536,
                },
            ),
        )
        cases.extend(
            [
                (
                    {"thinking_budget": 1024, "max_tokens": 100_000},
                    {"thinkingConfig": None, "maxOutputTokens": None},
                    {"thinking_config": None, "max_output_tokens": None},
                ),
                (
                    {"thinking_level": "high", "max_tokens": 100_000},
                    {
                        "thinkingConfig": {"thinkingLevel": "LOW"},
                        "maxOutputTokens": 65_536,
                        "topP": 0.3,
                        "topK": 20,
                    },
                    {
                        "thinking_config": {"thinking_level": "LOW"},
                        "max_output_tokens": 65_536,
                    },
                ),
            ],
        )
        for stream in (False, True):
            for parameters, overrides, expected in cases:
                with self.subTest(stream=stream, parameters=parameters):
                    self.model.stream = stream
                    self.model.parameters = GeminiChatModel.Parameters(
                        **parameters,
                    )
                    api = AsyncMock(return_value=_mock_completion([]))
                    stream_api = AsyncMock(return_value=_MockAsyncStream([]))
                    self.model.client.aio.models.generate_content = api
                    self.model.client.aio.models.generate_content_stream = (
                        stream_api
                    )
                    response = await self.model([], **overrides)
                    if stream:
                        async for _ in response:
                            pass
                    expected_calls = [
                        call(
                            model=self.model_name,
                            contents=[],
                            config=expected,
                        ),
                    ]
                    self.assertEqual(
                        api.await_args_list,
                        [] if stream else expected_calls,
                    )
                    self.assertEqual(
                        stream_api.await_args_list,
                        expected_calls if stream else [],
                    )

    async def test_ignored_sampling_parameters_warn(self) -> None:
        """Warn for non-null sampling values from either config source."""
        cases = [
            (
                {"temperature": 0.2, "top_p": 0.3},
                {},
                ["temperature", "top_p"],
            ),
            (
                {},
                {"temperature": 0, "topP": 0.3, "topK": 20},
                ["temperature", "top_p", "top_k"],
            ),
            (
                {"temperature": 0.2, "top_p": 0.3},
                {"temperature": None, "topP": None, "topK": None},
                [],
            ),
        ]
        for parameters, overrides, ignored in cases:
            with self.subTest(parameters=parameters, overrides=overrides):
                self.model.parameters = GeminiChatModel.Parameters(
                    **parameters,
                )
                api = AsyncMock(return_value=_mock_completion([]))
                self.model.client.aio.models.generate_content = api
                with patch(
                    "agentscope.model._gemini._model.logger.warning",
                ) as warning:
                    await self.model([], **overrides)
                self.assertEqual(
                    warning.call_args_list,
                    [
                        call(
                            "Gemini 4 models ignore %s; "
                            "the parameter will not be sent.",
                            key,
                        )
                        for key in ignored
                    ],
                )
                self.assertEqual(
                    api.await_args_list,
                    [call(model=self.model_name, contents=[], config={})],
                )

    async def test_invalid_config_never_reaches_api(self) -> None:
        """Reject unsupported budgets, reasoning levels, and long decode."""
        level_message = (
            f"{self.model_name} thinking_level must be one of "
            f"{', '.join(self.thinking_levels)}."
        )
        cases: list[tuple[dict[str, Any], dict[str, Any], str]] = [
            ({"thinking_budget": 1024}, {}, "thinking_budget"),
            ({}, {"thinking_config": {"thinking_budget": 0}}, "budget"),
            ({}, {"thinking_config": {"thinkingBudget": 100}}, "budget"),
            ({}, {"thinkingConfig": {"thinkingBudget": 100}}, "budget"),
            (
                {},
                {"thinking_config": {"thinking_level": "MINIMAL"}},
                level_message,
            ),
            ({"max_tokens": 65_537}, {}, "65536"),
            ({}, {"max_output_tokens": 65_537}, "65536"),
            ({}, {"maxOutputTokens": 65_537}, "65536"),
            (
                {},
                {"thinkingConfig": {"thinkingLevel": "MINIMAL"}},
                level_message,
            ),
        ]
        if "xhigh" not in self.thinking_levels:
            cases.extend(
                [
                    ({"thinking_level": "xhigh"}, {}, level_message),
                    (
                        {},
                        {"thinkingConfig": {"thinkingLevel": "XHIGH"}},
                        level_message,
                    ),
                ],
            )
        for alias, name in (
            ("thinkingConfig", "thinking_config"),
            ("maxOutputTokens", "max_output_tokens"),
            ("topP", "top_p"),
            ("topK", "top_k"),
        ):
            cases.append(({}, {alias: None, name: None}, name))
        for alias, name, value in (
            ("thinkingBudget", "thinking_budget", 1024),
            ("thinkingLevel", "thinking_level", "LOW"),
            ("includeThoughts", "include_thoughts", True),
        ):
            cases.append(
                ({}, {"thinking_config": {alias: value, name: value}}, name),
            )
        self.model.formatter = MagicMock()
        self.model.formatter.format = AsyncMock(return_value=[])
        for stream in (False, True):
            self.model.stream = stream
            for parameters, kwargs, message in cases:
                with self.subTest(
                    stream=stream,
                    parameters=parameters,
                    **kwargs,
                ):
                    self.model.parameters = GeminiChatModel.Parameters(
                        **parameters,
                    )
                    self.model.client.reset_mock()
                    with self.assertRaisesRegex(ValueError, message) as error:
                        await self.model([], **kwargs)
                    if message == level_message:
                        self.assertEqual(
                            str(error.exception),
                            level_message,
                        )
                    self.assertEqual(self.model.client.mock_calls, [])
                    self.assertEqual(self.model.formatter.mock_calls, [])

    def test_timeout_does_not_mutate_shared_client_options(self) -> None:
        """Keep model defaults local for dict and SDK HTTP options."""
        cases: list[tuple[dict[str, Any] | HttpOptions, int]] = [
            ({"timeout": None}, 900_000),
            (HttpOptions(), 900_000),
            ({"timeout": 1234}, 1234),
            ({"timeout": 0}, 0),
            (HttpOptions(timeout=0), 0),
        ]
        for options, expected_timeout in cases:
            with self.subTest(options=options):
                shared = {"http_options": options}
                before = copy.deepcopy(shared)
                with patch("google.genai.Client") as client:
                    model = GeminiChatModel(
                        credential=GeminiCredential(api_key="test"),
                        model=f"models/{self.model_name}",
                        client_kwargs=shared,
                    )
                    self.assertEqual(
                        client.call_args.kwargs,
                        {
                            "api_key": "test",
                            "http_options": HttpOptions(
                                timeout=expected_timeout,
                            ),
                        },
                    )
                    self.assertEqual(shared, before)
                    self.assertIsNot(model.client_kwargs, shared)
                    GeminiChatModel(
                        credential=GeminiCredential(api_key="test"),
                        model="gemini-2.5-flash",
                        client_kwargs=shared,
                    )
                    self.assertEqual(
                        client.call_args.kwargs,
                        {"api_key": "test", **before},
                    )
                    self.assertEqual(shared, before)

    def test_model_card_parameters(self) -> None:
        """Expose supported controls and hide legacy reasoning settings."""
        cards = {card.name: card for card in GeminiChatModel.list_models()}
        card = cards[self.model_name]
        properties = card.parameter_schema["properties"]
        self.assertEqual(
            {
                "name": card.name,
                "label": card.label,
                "context_size": card.context_size,
                "output_size": card.output_size,
                "parameters": sorted(properties),
                "max_tokens": properties["max_tokens"]["maximum"],
                "thinking_level": properties["thinking_level"]["default"],
                "levels": properties["thinking_level"]["anyOf"],
                "summary_title": properties["thinking_enable"]["title"],
            },
            {
                "name": self.model_name,
                "label": self.model_label,
                "context_size": 1_048_576,
                "output_size": 65_536,
                "parameters": [
                    "max_tokens",
                    "thinking_enable",
                    "thinking_level",
                ],
                "max_tokens": 65_536,
                "thinking_level": self.default_thinking_level,
                "levels": [
                    {"enum": list(self.thinking_levels), "type": "string"},
                    {"type": "null"},
                ],
                "summary_title": "Thought Summaries",
            },
        )
        self.assertEqual(
            {
                name: "thinking_level" in item.parameter_schema["properties"]
                for name, item in cards.items()
                if not GeminiChatModel._is_gemini_4(name)
            },
            {
                name: False
                for name in cards
                if name
                not in (
                    "gemini-4-argon",
                    "gemini-4-carbon",
                    "gemini-4-hydrogen",
                )
            },
        )


class TestGeminiApiVersion(IsolatedAsyncioTestCase):
    """Exercise SDK routing and the httpx2 transport without network calls."""

    async def test_request_versions_and_transport(self) -> None:
        """Preserve version precedence and SDK platform selection."""
        cases: list[
            tuple[dict[str, Any], dict[str, str], str | None, str | None, str]
        ] = [
            ({}, {}, None, None, "v1beta"),
            ({}, {}, "v1", None, "v1"),
            ({}, {}, "v1", "v1beta", "v1beta"),
            ({}, {}, None, "v1", "v1"),
            ({}, {"GOOGLE_GENAI_API_VERSION": "v1"}, None, None, "v1"),
            ({}, {"GOOGLE_GENAI_API_VERSION": "beta"}, None, None, "v1beta"),
            ({}, {"GOOGLE_GENAI_API_VERSION": "v1"}, "v1beta", None, "v1beta"),
            ({"vertexai": True}, {}, None, None, "v1beta1"),
            ({"enterprise": True}, {}, None, None, "v1beta1"),
            ({}, {"GOOGLE_GENAI_USE_VERTEXAI": "true"}, None, None, "v1beta1"),
            (
                {},
                {"GOOGLE_GENAI_USE_ENTERPRISE": "true"},
                None,
                None,
                "v1beta1",
            ),
        ]
        for stream in (False, True):
            for (
                platform,
                env,
                client_version,
                request_version,
                expected,
            ) in cases:
                with self.subTest(
                    stream=stream,
                    platform=platform,
                    env=env,
                    client=client_version,
                    request=request_version,
                ):
                    captured: list[str] = []

                    def handle(
                        request: httpx2.Request,
                        record: Callable[[str], None] = captured.append,
                        streaming: bool = stream,
                    ) -> httpx2.Response:
                        record(request.url.path.split("/")[1])
                        if streaming:
                            return httpx2.Response(
                                200,
                                headers={"content-type": "text/event-stream"},
                                text='data: {"candidates": []}\n\n',
                            )
                        return httpx2.Response(200, json={"candidates": []})

                    client_options = {
                        "api_version": client_version,
                        "async_client_args": {
                            "transport": httpx2.MockTransport(handle),
                        },
                    }
                    request_options = HttpOptions(
                        api_version=request_version,
                        timeout=12345,
                    )
                    before = request_options.model_dump(exclude_none=True)
                    with patch.dict(os.environ, env, clear=True):
                        model = GeminiChatModel(
                            credential=GeminiCredential(api_key="test"),
                            model="gemini-4-hydrogen"
                            if stream
                            else "gemini-2.5-flash",
                            stream=stream,
                            client_kwargs={
                                **platform,
                                "http_options": client_options
                                if stream
                                else HttpOptions(**client_options),
                            },
                        )
                        try:
                            backend = model.client._api_client
                            is_httpx2 = isinstance(
                                backend._async_httpx_client,
                                httpx2.AsyncClient,
                            )
                            response = await model(
                                [UserMsg(name="user", content="Hello")],
                                **{
                                    "httpOptions"
                                    if stream
                                    else "http_options": request_options
                                    if stream
                                    else before.copy(),
                                },
                            )
                            if stream:
                                async for _ in response:
                                    pass
                        finally:
                            try:
                                await model.client.aio.aclose()
                            finally:
                                model.client.close()
                    self.assertEqual((captured, is_httpx2), ([expected], True))
                    self.assertEqual(
                        request_options.model_dump(exclude_none=True),
                        before,
                    )
                    self.assertEqual(
                        client_options["api_version"],
                        client_version,
                    )


class TestGemini4Carbon(TestGemini4Argon):
    """Run the shared Gemini 4 contract against Carbon as well."""

    model_name = "gemini-4-carbon"
    model_label = "Gemini 4 Carbon"


class TestGemini4Hydrogen(TestGemini4Argon):
    """Verify Hydrogen's fourth thinking level and shared API contract."""

    model_name = "gemini-4-hydrogen"
    model_label = "Gemini 4 Hydrogen"
    thinking_levels = ("low", "medium", "high", "xhigh")
    default_thinking_level = "high"

    async def test_xhigh_serialized_by_sdk(self) -> None:
        """Verify the installed SDK sends xhigh in the actual JSON body."""
        captured = []

        def handle(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"candidates": []})

        model = GeminiChatModel(
            credential=GeminiCredential(api_key="test"),
            model=self.model_name,
            stream=False,
            parameters=GeminiChatModel.Parameters(thinking_level="xhigh"),
            client_kwargs={
                "http_options": {
                    "async_client_args": {
                        "transport": httpx.MockTransport(handle),
                    },
                },
            },
        )
        try:
            await model([UserMsg(name="user", content="Hello")])
        finally:
            try:
                await model.client.aio.aclose()
            finally:
                model.client.close()
        self.assertEqual(
            captured,
            [
                {
                    "contents": [
                        {"role": "user", "parts": [{"text": "Hello"}]},
                    ],
                    "generationConfig": {
                        # The SDK emits the original proto field name.
                        "thinkingConfig": {"thinking_level": "XHIGH"},
                    },
                },
            ],
        )

    async def test_qualified_model_with_sdk_thinking_config(self) -> None:
        """Accept xhigh for qualified IDs and normalize SDK aliases."""
        self.model.model = "models/gemini-4-hydrogen"
        api = AsyncMock(return_value=_mock_completion([]))
        self.model.client.aio.models.generate_content = api
        await self.model(
            [],
            thinkingConfig=ThinkingConfig(thinking_level="XHIGH"),
        )
        self.assertEqual(
            api.await_args_list,
            [
                call(
                    model="models/gemini-4-hydrogen",
                    contents=[],
                    config={"thinking_config": {"thinking_level": "XHIGH"}},
                ),
            ],
        )


# ---------------------------------------------------------------------------
# Non-streaming tests
# ---------------------------------------------------------------------------


class TestGeminiNonStream(IsolatedAsyncioTestCase):
    """Tests for GeminiChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)
        # Client is built eagerly in __init__; inject a mock onto the
        # instance so generate_content() hits it instead of the network.
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_thinking_level_rejected_for_legacy_models(self) -> None:
        """Keep the Gemini 4 reasoning control scoped to supported IDs."""
        self.model.parameters.thinking_level = "low"
        for name in ("gemini-2.5-flash", "gemini-4-carbon-unknown"):
            with self.subTest(model=name):
                self.model.model = name
                with self.assertRaisesRegex(ValueError, "thinking_level"):
                    await self.model([])
                self.assertEqual(self.mock_client.mock_calls, [])

    async def test_text_response(self) -> None:
        """Non-stream text response returns a single ChatResponse."""
        parts = [_make_part(text="Hello!")]
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion(parts),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [TextBlock.model_construct(id=A, created_at=A, text="Hello!")],
            ),
        )

    async def test_call_config_overrides_parameter_defaults(self) -> None:
        """Per-call config overrides model-level defaults."""
        self.model.parameters.max_tokens = 1024
        self.model.parameters.temperature = 0.2
        self.model.parameters.top_p = 0.3
        self.model.parameters.thinking_enable = True
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion([_make_part(text="ok")]),
        )

        default_config = {
            "max_output_tokens": 1024,
            "temperature": 0.2,
            "top_p": 0.3,
            "thinking_config": {
                "include_thoughts": True,
                "thinking_budget": 1024,
            },
        }
        cases = {
            "max_output_tokens": 2048,
            "temperature": 0.8,
            "top_p": 0.9,
            "thinking_config": {
                "include_thoughts": True,
                "thinking_budget": 8192,
            },
        }

        for key, value in cases.items():
            with self.subTest(key=key):
                await self.model([], **{key: value})

                call_args = (
                    self.mock_client.aio.models.generate_content.call_args
                )
                sent_config = call_args.kwargs["config"]
                self.assertEqual(
                    sent_config,
                    {**default_config, key: value},
                )

    async def test_thinking_config_omitted_by_default(self) -> None:
        """Default parameters send no ``thinking_config`` so the model's
        default behavior applies (e.g. Pro models think by default and
        reject ``thinking_budget: 0``)."""
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion([_make_part(text="ok")]),
        )
        self.model.parameters = GeminiChatModel.Parameters()

        await self.model([])

        sent_config = (
            self.mock_client.aio.models.generate_content.call_args.kwargs[
                "config"
            ]
        )
        self.assertEqual(sent_config, {})

    async def test_thinking_config_false_forwarded(self) -> None:
        """``thinking_enable=False`` sends the disable configuration."""
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion([_make_part(text="ok")]),
        )
        self.model.parameters = GeminiChatModel.Parameters(
            thinking_enable=False,
        )

        await self.model([])

        sent_config = (
            self.mock_client.aio.models.generate_content.call_args.kwargs[
                "config"
            ]
        )
        self.assertEqual(
            sent_config,
            {
                "thinking_config": {
                    "include_thoughts": False,
                    "thinking_budget": 0,
                },
            },
        )

    async def test_thinking_config_true_forwarded(self) -> None:
        """Enabled thinking uses the default or explicitly provided budget."""
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion([_make_part(text="ok")]),
        )

        for budget, expected_budget in [(None, 1024), (8192, 8192)]:
            with self.subTest(thinking_budget=budget):
                self.model.parameters = GeminiChatModel.Parameters(
                    thinking_enable=True,
                    thinking_budget=budget,
                )

                await self.model([])

                call_args = (
                    self.mock_client.aio.models.generate_content.call_args
                )
                self.assertEqual(
                    call_args.kwargs["config"],
                    {
                        "thinking_config": {
                            "include_thoughts": True,
                            "thinking_budget": expected_budget,
                        },
                    },
                )

    async def test_tool_call_response(self) -> None:
        """Non-stream tool call response creates ToolCallBlocks."""
        parts = [
            _make_part(
                function_call={
                    "name": "get_weather",
                    "args": {"city": "Tokyo"},
                    "id": "call-1",
                },
            ),
        ]
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion(parts),
        )

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
                        input=json.dumps(
                            {"city": "Tokyo"},
                            ensure_ascii=False,
                        ),
                    ),
                ],
            ),
        )

    async def test_tool_call_response_without_id(self) -> None:
        """A function call with no id gets a generated id, not a crash."""
        parts = [
            _make_part(
                function_call={
                    "name": "get_weather",
                    "args": {"city": "Tokyo"},
                    "id": None,
                },
            ),
        ]
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion(parts),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock.model_construct(
                        id=A,
                        created_at=A,
                        name="get_weather",
                        input=json.dumps(
                            {"city": "Tokyo"},
                            ensure_ascii=False,
                        ),
                    ),
                ],
            ),
        )
        self.assertIsInstance(result.content[0].id, str)
        self.assertTrue(result.content[0].id)

    async def test_thinking_response(self) -> None:
        """Non-stream response with reasoning creates ThinkingBlock."""
        parts = [
            _make_part(text="Let me think...", thought=True),
            _make_part(text="Answer"),
        ]
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion(parts),
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
                        thinking="Let me think...",
                    ),
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Answer",
                    ),
                ],
            ),
        )

    async def test_usage_normalizes_optional_cache_count(self) -> None:
        """SDK cache counts remain integer-valued in raw model usage."""
        from google.genai.types import GenerateContentResponseUsageMetadata

        for cache_count, expected in [(None, 0), (0, 0), (5, 5)]:
            with self.subTest(cache_count=cache_count):
                resp = _mock_completion([_make_part(text="hello")])
                resp.usage_metadata = GenerateContentResponseUsageMetadata(
                    prompt_token_count=10,
                    candidates_token_count=5,
                    total_token_count=15,
                    cached_content_token_count=cache_count,
                )
                self.mock_client.aio.models.generate_content = AsyncMock(
                    return_value=resp,
                )

                result = await self.model([])

                self.assertEqual(
                    dict(result.usage),
                    {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "time": result.usage.time,
                        "cache_creation_input_tokens": 0,
                        "cache_input_tokens": expected,
                        "type": "chat",
                        "metadata": None,
                    },
                )
                self.assertIs(type(result.usage.cache_input_tokens), int)
                usage = Usage(**dict(result.usage))
                self.assertEqual(
                    usage.model_dump(),
                    {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_input_tokens": expected,
                        "cache_creation_input_tokens": 0,
                    },
                )

    async def test_usage_classifies_tool_use_tokens_as_input(self) -> None:
        """Usage classifies tool-use tokens as input, not output."""
        parts = [_make_part(text="hello")]
        resp = _mock_completion(parts)
        # Mirror the SDK's documented invariant: total = prompt +
        # candidates + tool_use_prompt + thoughts.
        resp.usage_metadata = SimpleNamespace(
            prompt_token_count=500,
            candidates_token_count=120,
            tool_use_prompt_token_count=300,
            thoughts_token_count=10,
            total_token_count=930,
            cached_content_token_count=50,
        )
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=resp,
        )

        result = await self.model([])

        self.assertEqual(
            dict(result.usage),
            {
                "input_tokens": 800,
                "output_tokens": 130,
                "time": result.usage.time,
                "cache_creation_input_tokens": 0,
                "cache_input_tokens": 50,
                "type": "chat",
                "metadata": None,
            },
        )

    async def test_usage_fallback_excludes_tool_use_tokens(self) -> None:
        """Usage fallback excludes tool-use tokens from output."""
        resp = _mock_completion([_make_part(text="hello")])
        resp.usage_metadata = SimpleNamespace(
            prompt_token_count=500,
            tool_use_prompt_token_count=300,
            thoughts_token_count=10,
            total_token_count=810,
            cached_content_token_count=0,
        )
        self.mock_client.aio.models.generate_content = AsyncMock(
            return_value=resp,
        )

        result = await self.model([])

        self.assertEqual(
            dict(result.usage),
            {
                "input_tokens": 800,
                "output_tokens": 10,
                "time": result.usage.time,
                "cache_creation_input_tokens": 0,
                "cache_input_tokens": 0,
                "type": "chat",
                "metadata": None,
            },
        )


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestGeminiStream(IsolatedAsyncioTestCase):
    """Tests for GeminiChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)
        # Client is built eagerly in __init__; inject a mock onto the
        # instance so generate_content_stream() hits it instead of the
        # network.
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_stream_text(self) -> None:
        """Stream text yields n deltas + 1 final with full content."""
        chunks = [
            _make_stream_chunk([_make_part(text="Hello")]),
            _make_stream_chunk([_make_part(text=" world")]),
        ]
        self.mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=_MockAsyncStream(chunks),
        )

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

    async def test_stream_usage_normalizes_optional_cache_count(self) -> None:
        """Delta and final usage normalize optional SDK cache counts."""
        from google.genai.types import GenerateContentResponseUsageMetadata

        for cache_count, expected in [(None, 0), (0, 0), (5, 5)]:
            with self.subTest(cache_count=cache_count):
                chunk = _make_stream_chunk([_make_part(text="hello")])
                chunk.usage_metadata = GenerateContentResponseUsageMetadata(
                    prompt_token_count=10,
                    candidates_token_count=5,
                    total_token_count=15,
                    cached_content_token_count=cache_count,
                )
                self.mock_client.aio.models.generate_content_stream = (
                    AsyncMock(
                        return_value=_MockAsyncStream([chunk]),
                    )
                )

                gen = await self.model([])
                responses = [r async for r in gen]

                self.assertEqual(len(responses), 2)
                for response in responses:
                    self.assertEqual(
                        dict(response.usage),
                        {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "time": response.usage.time,
                            "cache_creation_input_tokens": 0,
                            "cache_input_tokens": expected,
                            "type": "chat",
                            "metadata": None,
                        },
                    )
                    self.assertIs(type(response.usage.cache_input_tokens), int)
                    usage = Usage(**dict(response.usage))
                    self.assertEqual(
                        usage.model_dump(),
                        {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "cache_input_tokens": expected,
                            "cache_creation_input_tokens": 0,
                        },
                    )

    async def test_stream_usage_classifies_tool_use_tokens(self) -> None:
        """Streaming usage classifies tool-use tokens as input."""
        chunk = _make_stream_chunk([_make_part(text="hello")])
        chunk.usage_metadata = SimpleNamespace(
            prompt_token_count=500,
            candidates_token_count=120,
            tool_use_prompt_token_count=300,
            thoughts_token_count=10,
            total_token_count=930,
            cached_content_token_count=50,
        )
        self.mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=_MockAsyncStream([chunk]),
        )

        gen = await self.model([])
        responses = [r async for r in gen]

        # The delta and the accumulated final response both carry the
        # usage of the last chunk.
        self.assertEqual(
            dict(responses[0].usage),
            {
                "input_tokens": 800,
                "output_tokens": 130,
                "time": responses[0].usage.time,
                "cache_creation_input_tokens": 0,
                "cache_input_tokens": 50,
                "type": "chat",
                "metadata": None,
            },
        )
        self.assertEqual(responses[-1].usage, responses[0].usage)

    async def test_stream_thinking_and_text(self) -> None:
        """Stream thinking + text yields deltas then accumulated final."""
        chunks = [
            _make_stream_chunk([_make_part(text="Think", thought=True)]),
            _make_stream_chunk([_make_part(text="Answer")]),
        ]
        self.mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=_MockAsyncStream(chunks),
        )

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
                            thinking="Think",
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
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="Think",
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

    async def test_stream_tool_call(self) -> None:
        """Stream tool call yields delta then final with same ToolCallBlock."""
        chunks = [
            _make_stream_chunk(
                [
                    _make_part(
                        function_call={
                            "name": "search",
                            "args": {"q": "test"},
                            "id": "call-1",
                        },
                    ),
                ],
            ),
        ]
        self.mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=_MockAsyncStream(chunks),
        )

        gen = await self.model([])
        responses = [r async for r in gen]

        tool_block = ToolCallBlock.model_construct(
            id="call-1",
            created_at=A,
            name="search",
            input=json.dumps({"q": "test"}, ensure_ascii=False),
        )
        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [tool_block]),
                (True, [tool_block]),
            ],
        )

    async def test_stream_tool_calls_without_id(self) -> None:
        """Two id-less function calls in one chunk get distinct ids."""
        chunks = [
            _make_stream_chunk(
                [
                    _make_part(
                        function_call={
                            "name": "search",
                            "args": {"q": "a"},
                            "id": None,
                        },
                    ),
                    _make_part(
                        function_call={
                            "name": "search",
                            "args": {"q": "b"},
                            "id": None,
                        },
                    ),
                ],
            ),
        ]
        self.mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=_MockAsyncStream(chunks),
        )

        gen = await self.model([])
        responses = [r async for r in gen]

        final = responses[-1]
        self.assertTrue(final.is_last)
        self.assertEqual(len(final.content), 2)
        ids = [block.id for block in final.content]
        self.assertTrue(all(isinstance(i, str) and i for i in ids))
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(
            [block.input for block in final.content],
            [
                json.dumps({"q": "a"}, ensure_ascii=False),
                json.dumps({"q": "b"}, ensure_ascii=False),
            ],
        )


# ---------------------------------------------------------------------------
# Thought signature round-trip tests
# ---------------------------------------------------------------------------


class TestGeminiThoughtSignatures(IsolatedAsyncioTestCase):
    """Keep signatures in marked IDs without changing message schemas."""

    async def test_tool_call_round_trip(self) -> None:
        """Preserve parallel and sequential calls across JSON storage."""
        for streaming in (False, True):
            for provider_id in (None, "call-original", "Y2FsbA=="):
                with self.subTest(stream=streaming, id=provider_id):
                    with patch("google.genai.Client") as client_class:
                        model = _make_model(stream=streaming)
                    client = client_class.return_value
                    history = [
                        Msg(
                            name="user",
                            role="user",
                            content=[TextBlock(text="Weather?")],
                        ),
                    ]
                    expected_history = [
                        {"role": "user", "parts": [{"text": "Weather?"}]},
                    ]

                    # Only the first of the parallel calls has a signature.
                    # The next step gets a different signature in this turn.
                    for step, signature in enumerate(
                        (bytes(range(256)), b"next-step-signature"),
                    ):
                        call_id = "gemini_ts:" + base64.b64encode(
                            signature,
                        ).decode("ascii")
                        parts = [
                            _make_part(
                                function_call={
                                    "id": provider_id,
                                    "name": "get_weather",
                                    "args": {"city": "Tokyo"},
                                },
                                thought_signature=signature,
                            ),
                        ]
                        if step == 0:
                            parts.append(
                                _make_part(
                                    function_call={
                                        "id": provider_id,
                                        "name": "get_weather",
                                        "args": {"city": "Paris"},
                                    },
                                ),
                            )
                        if streaming:
                            generate = AsyncMock(
                                return_value=_MockAsyncStream(
                                    [_make_stream_chunk([p]) for p in parts],
                                ),
                            )
                            client.aio.models.generate_content_stream = (
                                generate
                            )
                            responses = [r async for r in await model(history)]
                            result = responses[-1]
                        else:
                            generate = AsyncMock(
                                return_value=_mock_completion(parts),
                            )
                            client.aio.models.generate_content = generate
                            result = await model(history)

                        self.assertEqual(
                            generate.await_args.kwargs["contents"],
                            expected_history,
                        )
                        expected_blocks = [
                            ToolCallBlock.model_construct(
                                id=call_id,
                                created_at=A,
                                name="get_weather",
                                input=json.dumps({"city": "Tokyo"}),
                            ),
                        ]
                        if step == 0:
                            expected_blocks.append(
                                ToolCallBlock.model_construct(
                                    id=provider_id or A,
                                    created_at=A,
                                    name="get_weather",
                                    input=json.dumps({"city": "Paris"}),
                                ),
                            )
                        self.assertEqual(result.content, expected_blocks)
                        self.assertEqual(
                            len({b.id for b in result.content}),
                            len(result.content),
                        )
                        message = AssistantMsg(
                            name="assistant",
                            content=[
                                *result.content,
                                *[
                                    ToolResultBlock(
                                        id=b.id,
                                        name=b.name,
                                        output="sunny",
                                    )
                                    for b in result.content
                                ],
                            ],
                        )
                        history.append(
                            Msg.model_validate_json(message.model_dump_json()),
                        )
                        expected_parts = []
                        expected_results = []
                        for block, part in zip(result.content, parts):
                            expected_part = {
                                "function_call": {
                                    "id": block.id,
                                    "name": "get_weather",
                                    "args": part.function_call.args,
                                },
                            }
                            if part.thought_signature:
                                expected_part["thought_signature"] = signature
                            expected_parts.append(expected_part)
                            expected_results.append(
                                {
                                    "function_response": {
                                        "id": block.id,
                                        "name": "get_weather",
                                        "response": {"output": "sunny"},
                                    },
                                },
                            )
                        expected_history.extend(
                            [
                                {"role": "model", "parts": expected_parts},
                                {"role": "user", "parts": expected_results},
                            ],
                        )
                        self.assertEqual(
                            await GeminiChatFormatter().format(history),
                            expected_history,
                        )

    async def test_unmarked_ids_are_not_decoded(self) -> None:
        """Do not infer signatures from plain or legacy Base64 IDs."""
        for call_id in ("call-1", "Y2FsbA==", "bGVnYWN5LXNpZ25hdHVyZQ=="):
            with self.subTest(id=call_id):
                message = AssistantMsg(
                    name="assistant",
                    content=[
                        ToolCallBlock(id=call_id, name="search", input="{}"),
                    ],
                )
                self.assertEqual(
                    await GeminiChatFormatter().format([message]),
                    [
                        {
                            "role": "model",
                            "parts": [
                                {
                                    "function_call": {
                                        "id": call_id,
                                        "name": "search",
                                        "args": {},
                                    },
                                },
                            ],
                        },
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

_FT_TOOLS_GEMINI = [
    {
        "function_declarations": [
            {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
            {
                "name": "get_time",
                "description": "Get the time",
                "parameters": {
                    "type": "object",
                    "properties": {"timezone": {"type": "string"}},
                    "required": ["timezone"],
                },
            },
        ],
    },
]


class TestGeminiFormatTools(unittest.TestCase):
    """Tests for GeminiChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode returns function_declarations and AUTO config."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "AUTO"}},
        )

    def test_none_mode(self) -> None:
        """None mode returns function_declarations and NONE config."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "NONE"}},
        )

    def test_required_mode(self) -> None:
        """Required mode maps to ANY config."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="required"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "ANY"}},
        )

    def test_str_mode_force_call(self) -> None:
        """A specific tool name restricts via allowed_function_names."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertEqual(
            fmt_choice,
            {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": ["get_weather"],
                },
            },
        )

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, _ = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools[0]["function_declarations"]), 1)
        self.assertEqual(
            fmt_tools[0]["function_declarations"][0]["name"],
            "get_weather",
        )

    def test_no_tool_choice(self) -> None:
        """Without tool_choice, returns function_declarations and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertIsNone(fmt_choice)

    def test_schema_keyword_removed_from_parameters(self) -> None:
        """The unsupported `$schema` keyword is removed before the call."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search for text",
                    "parameters": {
                        "$schema": "https://example.com/schema.json",
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                        },
                    },
                },
            },
        ]

        fmt_tools, _ = self.model._format_tools(tools, None)
        parameters = fmt_tools[0]["function_declarations"][0]["parameters"]

        self.assertNotIn("$schema", parameters)


# ---------------------------------------------------------------------------
# Tests for _sanitize_schema_for_gemini and _flatten_json_schema
# ---------------------------------------------------------------------------


class TestGeminiSchemaUtils(unittest.TestCase):
    """Tests for _sanitize_schema_for_gemini and _flatten_json_schema."""

    def test_sanitize_removes_schema_keyword_recursively(self) -> None:
        """The unsupported `$schema` keyword is removed recursively."""
        result = _sanitize_schema_for_gemini(
            {
                "$schema": "https://example.com/schema.json",
                "type": "object",
                "properties": {
                    "query": {
                        "$schema": "https://example.com/schema.json",
                        "type": "string",
                    },
                },
            },
        )

        self.assertEqual(
            result,
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
            },
        )

    def test_sanitize_removes_additional_properties_and_inlines_optional(
        self,
    ) -> None:
        """additionalProperties removed; anyOf[X, null] inlined to X."""
        self.assertEqual(
            _sanitize_schema_for_gemini(
                {
                    "description": "x",
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                },
            ),
            {"type": "string", "description": "x"},
        )
        self.assertEqual(
            _sanitize_schema_for_gemini(
                {"type": "object", "additionalProperties": False},
            ),
            {"type": "object"},
        )

    def test_sanitize_inlines_nullable_type_array(self) -> None:
        """JSON Schema type arrays with null are inlined for Gemini."""
        self.assertEqual(
            _sanitize_schema_for_gemini(
                {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": ["string", "null"],
                            "description": "Optional name",
                        },
                    },
                },
            ),
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional name",
                    },
                },
            },
        )

    def test_sanitize_converts_nullable_union_type_array_to_anyof(
        self,
    ) -> None:
        """Nullable type arrays with multiple non-null types use anyOf."""
        self.assertEqual(
            _sanitize_schema_for_gemini(
                {
                    "type": ["string", "integer", "null"],
                    "description": "String or integer identifier",
                },
            ),
            {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                ],
                "description": "String or integer identifier",
            },
        )

    def test_sanitize_rejects_ambiguous_type_array_with_anyof(
        self,
    ) -> None:
        """Existing anyOf constraints are not overwritten silently."""
        with self.assertRaisesRegex(
            ValueError,
            "multi-type nullable type array and anyOf",
        ):
            _sanitize_schema_for_gemini(
                {
                    "type": ["string", "integer", "null"],
                    "anyOf": [
                        {"enum": ["auto"]},
                        {"enum": [0]},
                        {"type": "null"},
                    ],
                },
            )

    def test_sanitize_inlines_annotated_null_anyof(self) -> None:
        """Annotated null schemas in anyOf are treated as null branches."""
        self.assertEqual(
            _sanitize_schema_for_gemini(
                {
                    "description": "Optional name",
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "null",
                            "description": "No value",
                        },
                    ],
                },
            ),
            {
                "type": "string",
                "description": "Optional name",
            },
        )

    def test_sanitize_pydantic_optional_list_dict(self) -> None:
        """End-to-end: Pydantic Optional[list[dict]] schema is cleaned."""
        result = _sanitize_schema_for_gemini(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "actions": {
                        "description": "List of actions",
                        "anyOf": [
                            {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": True,
                                },
                            },
                            {"type": "null"},
                        ],
                    },
                },
            },
        )
        self.assertEqual(
            result,
            {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "description": "List of actions",
                        "items": {"type": "object"},
                    },
                },
            },
        )

    def test_sanitize_converts_const_to_enum(self) -> None:
        """`const` is converted into a single-value `enum` for Gemini."""
        self.assertEqual(
            _sanitize_schema_for_gemini(
                {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "const": "general"},
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                    },
                    "required": ["query"],
                },
            ),
            {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "enum": ["general"]},
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                },
                "required": ["query"],
            },
        )

    def test_flatten_resolves_ref_and_removes_defs(self) -> None:
        """$ref inlined with extra keys merged; $defs removed."""
        self.assertEqual(
            _flatten_json_schema(
                {
                    "$defs": {"Name": {"type": "string"}},
                    "properties": {
                        "name": {
                            "$ref": "#/$defs/Name",
                            "description": "The name",
                        },
                    },
                },
            ),
            {
                "properties": {
                    "name": {"type": "string", "description": "The name"},
                },
            },
        )

    def test_flatten_escaped_definition_names(self) -> None:
        """JSON Pointer escapes are decoded once and in the correct order."""
        for keyword in ("$defs", "definitions"):
            for name, token in (
                ("a/b", "a~1b"),
                ("a~b", "a~0b"),
                ("a~1b", "a~01b"),
            ):
                with self.subTest(keyword=keyword, name=name):
                    ref = {
                        "$ref": f"#/{keyword}/{token}",
                        "description": "Value",
                    }
                    schema = {
                        keyword: {name: {"type": "string"}},
                        "properties": {"value": ref},
                    }
                    original = json.dumps(schema, sort_keys=True)
                    self.assertEqual(
                        _flatten_json_schema(schema),
                        {
                            "properties": {
                                "value": {
                                    "type": "string",
                                    "description": "Value",
                                },
                            },
                        },
                    )
                    self.assertEqual(
                        json.dumps(schema, sort_keys=True),
                        original,
                    )

    def test_flatten_escaped_recursive_definition(self) -> None:
        """Escaped names use the same decoded key for cycle detection."""
        result = _flatten_json_schema(
            {
                "$defs": {
                    "Node/Child": {
                        "type": "object",
                        "properties": {
                            "next": {"$ref": "#/$defs/Node~1Child"},
                        },
                    },
                },
                "properties": {"root": {"$ref": "#/$defs/Node~1Child"}},
            },
        )
        self.assertEqual(
            result["properties"]["root"]["properties"]["next"],
            {"type": "object", "description": "(circular: Node/Child)"},
        )

    def test_flatten_circular_ref_returns_placeholder(self) -> None:
        """Circular $ref produces a placeholder without infinite recursion."""
        self.assertEqual(
            _flatten_json_schema(
                {
                    "$defs": {
                        "Node": {
                            "type": "object",
                            "properties": {"child": {"$ref": "#/$defs/Node"}},
                        },
                    },
                    "properties": {"root": {"$ref": "#/$defs/Node"}},
                },
            ),
            {
                "properties": {
                    "root": {
                        "type": "object",
                        "properties": {
                            "child": {
                                "type": "object",
                                "description": "(circular: Node)",
                            },
                        },
                    },
                },
            },
        )

    def test_flatten_boolean_schema_ref_with_siblings(self) -> None:
        """A $ref to a boolean schema keeps the siblings instead of failing."""
        self.assertEqual(
            _flatten_json_schema(
                {
                    "$defs": {"Anything": True, "Nothing": False},
                    "type": "object",
                    "properties": {
                        "free": {
                            "$ref": "#/$defs/Anything",
                            "description": "d",
                        },
                        "denied": {"$ref": "#/$defs/Nothing"},
                    },
                },
            ),
            {
                "type": "object",
                "properties": {
                    "free": {"allOf": [True], "description": "d"},
                    "denied": {"allOf": [False]},
                },
            },
        )

    def test_flatten_removes_local_discriminators(self) -> None:
        """Flattened unions retain constraints without dangling mappings."""

        class Ring(BaseModel):
            """One branch of the discriminated union."""

            kind: Literal["ring"]
            radius: float

        class Disc(BaseModel):
            """The other branch of the discriminated union."""

            kind: Literal["disc"]
            diameter: float

        class ShapeTool(BaseModel):
            """Tool schema containing a discriminated union."""

            shape: Annotated[Ring | Disc, Field(discriminator="kind")]

        class Drawing(BaseModel):
            """A branch containing another discriminated union."""

            kind: Literal["drawing"]
            shape: Annotated[Ring | Disc, Field(discriminator="kind")]

        class DrawingTool(BaseModel):
            """Tool schema containing nested discriminated unions."""

            drawing: Annotated[Drawing | Ring, Field(discriminator="kind")]

        ring = {
            "description": "One branch of the discriminated union.",
            "properties": {
                "kind": {"const": "ring", "title": "Kind", "type": "string"},
                "radius": {"title": "Radius", "type": "number"},
            },
            "required": ["kind", "radius"],
            "title": "Ring",
            "type": "object",
        }
        disc = {
            "description": "The other branch of the discriminated union.",
            "properties": {
                "kind": {"const": "disc", "title": "Kind", "type": "string"},
                "diameter": {"title": "Diameter", "type": "number"},
            },
            "required": ["kind", "diameter"],
            "title": "Disc",
            "type": "object",
        }
        shape = {
            "oneOf": [ring, disc],
            "title": "Shape",
        }
        for model, expected in (
            (
                ShapeTool,
                {
                    "description": (
                        "Tool schema containing a discriminated union."
                    ),
                    "properties": {"shape": shape},
                    "required": ["shape"],
                    "title": "ShapeTool",
                    "type": "object",
                },
            ),
            (
                DrawingTool,
                {
                    "description": (
                        "Tool schema containing nested discriminated unions."
                    ),
                    "properties": {
                        "drawing": {
                            "oneOf": [
                                {
                                    "description": (
                                        "A branch containing another "
                                        "discriminated union."
                                    ),
                                    "properties": {
                                        "kind": {
                                            "const": "drawing",
                                            "title": "Kind",
                                            "type": "string",
                                        },
                                        "shape": shape,
                                    },
                                    "required": ["kind", "shape"],
                                    "title": "Drawing",
                                    "type": "object",
                                },
                                ring,
                            ],
                            "title": "Drawing",
                        },
                    },
                    "required": ["drawing"],
                    "title": "DrawingTool",
                    "type": "object",
                },
            ),
        ):
            with self.subTest(model=model.__name__):
                schema = model.model_json_schema()
                original = copy.deepcopy(schema)
                self.assertEqual(_flatten_json_schema(schema), expected)
                self.assertEqual(schema, original)

    def test_flatten_removes_local_discriminators_from_anyof(self) -> None:
        """Local mappings are removed for both definition dialects."""
        branch = {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["ring"]}},
            "required": ["kind"],
        }
        for keyword in ("$defs", "definitions"):
            with self.subTest(keyword=keyword):
                ref = f"#/{keyword}/Ring"
                self.assertEqual(
                    _flatten_json_schema(
                        {
                            keyword: {"Ring": branch},
                            "anyOf": [{"$ref": ref}],
                            "discriminator": {
                                "propertyName": "kind",
                                "mapping": {
                                    "ring": ref,
                                    "remote": "https://example.com/schema",
                                },
                            },
                        },
                    ),
                    {"anyOf": [branch]},
                )

    def test_flatten_keeps_other_discriminators(self) -> None:
        """Mappings unrelated to removed definitions stay unchanged."""
        for discriminator in (
            {"propertyName": "kind"},
            {"propertyName": "kind", "mapping": {}},
            {
                "propertyName": "kind",
                "mapping": {
                    "missing": "#/$defs/Ghost",
                    "legacy": "#/definitions/Ghost",
                    "remote": "https://example.com/schema",
                    "implicit": "Ring",
                },
            },
        ):
            with self.subTest(discriminator=discriminator):
                self.assertEqual(
                    _flatten_json_schema(
                        {
                            "$defs": {"Ring": {"type": "object"}},
                            "discriminator": discriminator,
                        },
                    ),
                    {"discriminator": discriminator},
                )

        discriminator = {
            "propertyName": "kind",
            "mapping": {"ring": "#/$defs/Ring"},
        }
        self.assertEqual(
            _flatten_json_schema({"discriminator": discriminator}),
            {"discriminator": discriminator},
        )

    def test_flatten_keeps_property_names(self) -> None:
        """Schema keywords used as property names are preserved."""
        for keyword in (
            "properties",
            "patternProperties",
            "dependentSchemas",
            "dependencies",
        ):
            with self.subTest(keyword=keyword):
                self.assertEqual(
                    _flatten_json_schema(
                        {
                            "$defs": {"Name": {"type": "string"}},
                            keyword: {
                                "definitions": {"$ref": "#/$defs/Name"},
                                "$defs": {"$ref": "#/$defs/Name"},
                                "$ref": {"$ref": "#/$defs/Name"},
                            },
                        },
                    ),
                    {
                        keyword: {
                            "definitions": {"type": "string"},
                            "$defs": {"type": "string"},
                            "$ref": {"type": "string"},
                        },
                    },
                )

    def test_flatten_keeps_instance_values(self) -> None:
        """Instance data stays intact, including next to a schema $ref."""
        data = {
            "definitions": {"x": 1},
            "$defs": {"x": 2},
        }
        for keyword, value in (
            ("default", data),
            ("const", data),
            ("enum", [{"$ref": "#/$defs/Value"}]),
            ("examples", [{"nested": data}]),
        ):
            for with_ref in (False, True):
                with self.subTest(keyword=keyword, with_ref=with_ref):
                    prop = {keyword: value}
                    if with_ref:
                        prop["$ref"] = "#/$defs/Value"
                    else:
                        prop["type"] = "object"
                    self.assertEqual(
                        _flatten_json_schema(
                            {
                                "$defs": {"Value": {"type": "object"}},
                                "properties": {"value": prop},
                            },
                        ),
                        {
                            "properties": {
                                "value": {"type": "object", keyword: value},
                            },
                        },
                    )

    def test_flatten_legacy_definitions_ref(self) -> None:
        """Legacy 'definitions' keyword is resolved like '$defs'."""
        self.assertEqual(
            _flatten_json_schema(
                {
                    "definitions": {"Address": {"type": "string"}},
                    "properties": {
                        "addr": {"$ref": "#/definitions/Address"},
                    },
                },
            ),
            {
                "properties": {
                    "addr": {"type": "string"},
                },
            },
        )

    def test_flatten_no_defs_returns_same_object(self) -> None:
        """Schema with no $defs/definitions is returned unchanged (
        identity)."""
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        self.assertIs(_flatten_json_schema(schema), schema)
