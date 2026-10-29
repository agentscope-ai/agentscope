# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""K3.1 request contracts and compatibility with existing Moonshot models."""
from copy import deepcopy
from dataclasses import asdict
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call

from model_moonshot_test import (
    _make_model,
    _mock_completion,
    _MockAsyncStream,
    _make_stream_chunk,
    _make_tool_call_delta,
)
from utils import AnyString, AnyValue

from agentscope.exception import StructuredOutputError
from agentscope.formatter import (
    MoonshotChatFormatter,
    MoonshotMultiAgentFormatter,
)
from agentscope.message import (
    UserMsg,
    AssistantMsg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from agentscope.model import MoonshotChatModel, StructuredResponse
from agentscope.tool import ToolChoice

_MODELS = ("kimi-k3.1-pro", "kimi-k3.1-pro-highspeed")


class MoonshotK31Test(IsolatedAsyncioTestCase):
    """Verify the request body for both K3.1 variants."""

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
        for name in _MODELS:
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

    async def test_legacy_request_parameters(self) -> None:
        """Existing models keep their sampling and thinking behavior."""
        for name in (
            "kimi-k3",
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
                }
                if name == "kimi-k3":
                    expected.update(
                        max_completion_tokens=1024,
                        reasoning_effort="high",
                    )
                else:
                    expected.update(
                        max_tokens=1024,
                        extra_body={
                            "thinking": {"type": "disabled"},
                        },
                    )
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
        for name in _MODELS:
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

    def test_model_cards(self) -> None:
        """Both variants expose their full capabilities and editable schema."""
        cards = {card.name: card for card in MoonshotChatModel.list_models()}
        for name in _MODELS:
            with self.subTest(model=name):
                self.assertIn(name, cards)
                self.assertDictEqual(
                    cards[name].model_dump(),
                    {
                        "type": "chat_model",
                        "model_class": "moonshot_chat",
                        "name": name,
                        "label": "Kimi K3.1 Pro"
                        + (" Highspeed" if name.endswith("highspeed") else ""),
                        "status": "active",
                        "deprecated_at": None,
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

    async def test_reasoning_tool_round_trip(self) -> None:
        """Both response modes and formatters preserve a tool conversation."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look up a value",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        for name in _MODELS:
            for stream in (False, True):
                for formatter in (
                    MoonshotChatFormatter,
                    MoonshotMultiAgentFormatter,
                ):
                    with self.subTest(
                        model=name,
                        stream=stream,
                        formatter=formatter,
                    ):
                        model = _make_model(stream=stream)
                        model.model = name
                        model.formatter = formatter()
                        if stream:
                            response = _MockAsyncStream(
                                [
                                    _make_stream_chunk(
                                        delta_reasoning="Check",
                                    ),
                                    _make_stream_chunk(
                                        delta_text="Looking up",
                                    ),
                                    _make_stream_chunk(
                                        tool_calls=[
                                            _make_tool_call_delta(
                                                0,
                                                "call-1",
                                                "lookup",
                                                "{",
                                            ),
                                        ],
                                    ),
                                    _make_stream_chunk(
                                        tool_calls=[
                                            _make_tool_call_delta(
                                                0,
                                                arguments="}",
                                            ),
                                        ],
                                    ),
                                    _make_stream_chunk(
                                        has_choices=False,
                                        usage={
                                            "prompt_tokens": 10,
                                            "completion_tokens": 5,
                                        },
                                    ),
                                ],
                            )
                        else:
                            response = _mock_completion(
                                text="Looking up",
                                reasoning="Check",
                                tool_calls=[
                                    {
                                        "id": "call-1",
                                        "name": "lookup",
                                        "arguments": "{}",
                                    },
                                ],
                            )
                        create = AsyncMock(return_value=response)
                        model.client.chat.completions.create = create
                        result = await model(
                            [],
                            tools=tools,
                            tool_choice=ToolChoice(mode="required"),
                        )
                        if stream:
                            responses = [chunk async for chunk in result]
                            result = responses[-1]
                            self.assertListEqual(
                                [chunk.is_last for chunk in responses],
                                [False, False, False, False, True],
                            )
                        self.assertDictEqual(
                            asdict(result),
                            {
                                "id": "kimi-1",
                                "created_at": AnyString(),
                                "type": "chat_response",
                                "is_last": True,
                                "finished_reason": "completed",
                                "metadata": {},
                                "content": [
                                    ThinkingBlock.model_construct(
                                        id=AnyString(),
                                        created_at=AnyString(),
                                        thinking="Check",
                                    ),
                                    TextBlock.model_construct(
                                        id=AnyString(),
                                        created_at=AnyString(),
                                        text="Looking up",
                                    ),
                                    ToolCallBlock.model_construct(
                                        id="call-1",
                                        created_at=AnyString(),
                                        name="lookup",
                                        input="{}",
                                    ),
                                ],
                                "usage": {
                                    "type": "chat",
                                    "input_tokens": 10,
                                    "output_tokens": 5,
                                    "time": AnyValue(),
                                    "cache_input_tokens": 0,
                                    "cache_creation_input_tokens": 0,
                                    "metadata": None,
                                },
                            },
                        )
                        self.assertDictEqual(
                            create.call_args.kwargs,
                            {
                                "model": name,
                                "messages": [],
                                "stream": stream,
                                "tools": tools,
                                "tool_choice": "required",
                                **(
                                    {"stream_options": {"include_usage": True}}
                                    if stream
                                    else {}
                                ),
                            },
                        )
                        history = [
                            AssistantMsg(
                                "assistant",
                                [
                                    *result.content,
                                    ToolResultBlock(
                                        id="call-1",
                                        name="lookup",
                                        output=[TextBlock(text="42")],
                                    ),
                                ],
                            ),
                        ]
                        model.stream = False
                        create.return_value = _mock_completion(text="42")
                        await model(history, tools=tools)
                        self.assertDictEqual(
                            create.call_args.kwargs,
                            {
                                "model": name,
                                "stream": False,
                                "tools": tools,
                                "messages": [
                                    {
                                        "role": "assistant",
                                        "name": "assistant",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "Looking up",
                                            },
                                        ],
                                        "reasoning_content": "Check",
                                        "tool_calls": [
                                            {
                                                "id": "call-1",
                                                "type": "function",
                                                "function": {
                                                    "name": "lookup",
                                                    "arguments": "{}",
                                                },
                                            },
                                        ],
                                    },
                                    {
                                        "role": "tool",
                                        "name": "lookup",
                                        "tool_call_id": "call-1",
                                        "content": "42",
                                    },
                                ],
                            },
                        )
                        await model.client.close()
