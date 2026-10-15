# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for provider content-block normalization."""
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.credential import (
    AnthropicCredential,
    MiniMaxCredential,
    OpenAICredential,
)
from agentscope.exception import ContentBlockedError, ContentBlockedPhase
from agentscope.model import (
    AnthropicChatModel,
    DashScopeChatModel,
    DeepSeekChatModel,
    GeminiChatModel,
    MiniMaxChatModel,
    MoonshotChatModel,
    OpenAIChatModel,
    OpenAIResponseModel,
    VolcengineChatModel,
)


class _ProviderError(Exception):
    """A structured OpenAI-compatible provider error."""

    def __init__(self, body: dict, request_id: str) -> None:
        """Initialize the structured error payload."""
        super().__init__(body["message"])
        self.body = body
        self.request_id = request_id


class _Stream:
    """An async context-managed stream over fixed chunks."""

    def __init__(self, chunks: list) -> None:
        """Initialize the stream chunks."""
        self._chunks = iter(chunks)
        self.response = SimpleNamespace(request=None)

    async def __aenter__(self) -> "_Stream":
        """Enter the stream context."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the stream context."""

    def __aiter__(self) -> "_Stream":
        """Return the asynchronous iterator."""
        return self

    async def __anext__(self) -> Any:
        """Return the next chunk."""
        try:
            return next(self._chunks)
        except StopIteration as error:
            raise StopAsyncIteration from error


class ModelContentBlockTest(IsolatedAsyncioTestCase):
    """Test structured content-block signals from supported providers."""

    def test_refusal_option_defaults_and_opt_in(self) -> None:
        """Each supported constructor preserves the refusal opt-in."""
        providers = (
            (OpenAIChatModel, OpenAICredential, "openai.AsyncClient"),
            (OpenAIResponseModel, OpenAICredential, "openai.AsyncClient"),
            (
                AnthropicChatModel,
                AnthropicCredential,
                "anthropic.AsyncAnthropic",
            ),
            (MiniMaxChatModel, MiniMaxCredential, "anthropic.AsyncAnthropic"),
        )
        for model_class, credential_class, client_path in providers:
            for options in ({}, {"raise_on_refusal": True}):
                with self.subTest(model=model_class, options=options):
                    with patch(client_path):
                        model = model_class(
                            credential=credential_class(api_key="test"),
                            model="test-model",
                            **options,
                        )
                    self.assertEqual(
                        model.raise_on_refusal,
                        options.get("raise_on_refusal", False),
                    )

    async def test_dashscope_data_inspection_error(self) -> None:
        """DashScope inspection codes map to a normalized unknown phase."""
        model = object.__new__(DashScopeChatModel)
        original = _ProviderError(
            {
                "code": "DataInspectionFailed",
                "message": "inspection failed",
            },
            "dash-request",
        )

        error = model._normalize_exception(original)

        self.assertIsInstance(error, ContentBlockedError)
        self.assertDictEqual(
            {
                "message": error.message,
                "phase": error.phase,
                "provider": error.provider,
                "original_code": error.original_code,
                "request_id": error.request_id,
                "partial_output": error.partial_output,
            },
            {
                "message": "inspection failed",
                "phase": ContentBlockedPhase.UNKNOWN,
                "provider": "dashscope",
                "original_code": "DataInspectionFailed",
                "request_id": "dash-request",
                "partial_output": False,
            },
        )

    async def test_openai_content_filter_error(self) -> None:
        """OpenAI input filter codes map to an input content block."""
        model = object.__new__(OpenAIChatModel)
        original = _ProviderError(
            {
                "code": "content_filter",
                "message": "input blocked",
            },
            "openai-request",
        )

        error = model._normalize_exception(original)

        self.assertIsInstance(error, ContentBlockedError)
        self.assertEqual(error.phase, ContentBlockedPhase.INPUT)
        self.assertEqual(error.provider, "openai")
        self.assertEqual(error.original_code, "content_filter")
        self.assertEqual(error.request_id, "openai-request")

    async def test_openai_completion_finish_reason(self) -> None:
        """A non-stream content-filter finish reason is an output block."""
        model = object.__new__(OpenAIChatModel)
        response = SimpleNamespace(
            id="response-1",
            choices=[SimpleNamespace(finish_reason="content_filter")],
            usage=None,
        )

        with self.assertRaises(ContentBlockedError) as raised:
            model._parse_completion_response(datetime.now(), response)

        self.assertEqual(raised.exception.phase, ContentBlockedPhase.OUTPUT)
        self.assertEqual(raised.exception.request_id, "response-1")
        self.assertFalse(raised.exception.partial_output)

    async def test_openai_chat_refusal(self) -> None:
        """A Chat Completions refusal is an output content block."""
        model = object.__new__(OpenAIChatModel)
        model.raise_on_refusal = True
        response = SimpleNamespace(
            id="response-refusal",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(refusal="Cannot help."),
                ),
            ],
            usage=None,
        )

        with self.assertRaises(ContentBlockedError) as raised:
            model._parse_completion_response(datetime.now(), response)

        self.assertEqual(raised.exception.phase, ContentBlockedPhase.OUTPUT)
        self.assertEqual(raised.exception.original_code, "refusal")
        self.assertEqual(raised.exception.message, "Cannot help.")

    async def test_openai_chat_refusal_is_output_by_default(self) -> None:
        """A Chat Completions refusal remains visible by default."""
        model = object.__new__(OpenAIChatModel)
        response = SimpleNamespace(
            id="response-refusal",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=None,
                        refusal="Cannot help.",
                        audio=None,
                        tool_calls=[],
                    ),
                ),
            ],
            usage=None,
        )

        parsed = model._parse_completion_response(datetime.now(), response)

        self.assertListEqual(
            [block.text for block in parsed.content],
            ["Cannot help."],
        )

    async def test_openai_chat_stream_refusal_after_output(self) -> None:
        """A streamed Chat refusal records prior emitted output."""
        model = object.__new__(OpenAIChatModel)
        model.raise_on_refusal = True
        stream = _Stream(
            [
                SimpleNamespace(
                    id="response-refusal-stream",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(
                                refusal=None,
                                reasoning_content=None,
                                content="partial",
                                tool_calls=None,
                                audio=None,
                            ),
                        ),
                    ],
                ),
                SimpleNamespace(
                    id="response-refusal-stream",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            delta=SimpleNamespace(refusal="Cannot help."),
                        ),
                    ],
                ),
            ],
        )
        chunks = []

        with self.assertRaises(ContentBlockedError) as raised:
            async for chunk in model._parse_stream_response(
                datetime.now(),
                stream,
            ):
                chunks.append(chunk)

        self.assertListEqual(
            [chunk.content[0].text for chunk in chunks],
            ["partial"],
        )
        self.assertTrue(raised.exception.partial_output)

    async def test_openai_chat_stream_refusal_is_output_by_default(
        self,
    ) -> None:
        """Refusal fragments are returned as text with the default option."""
        model = object.__new__(OpenAIChatModel)
        stream = _Stream(
            [
                SimpleNamespace(
                    id="response-refusal-stream",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(refusal=fragment),
                        ),
                    ],
                )
                for fragment in ("Cannot ", "help.")
            ],
        )

        chunks = [
            chunk
            async for chunk in model._parse_stream_response(
                datetime.now(),
                stream,
            )
        ]

        self.assertEqual(
            [block.text for chunk in chunks for block in chunk.content],
            ["Cannot ", "help."],
        )

    async def test_dashscope_stream_finish_reason_after_output(self) -> None:
        """A streamed filter signal records previously emitted output."""
        model = object.__new__(DashScopeChatModel)
        text_delta = SimpleNamespace(
            reasoning_content=None,
            content="partial",
            tool_calls=None,
            audio=None,
        )
        empty_delta = SimpleNamespace(
            reasoning_content=None,
            content=None,
            tool_calls=None,
            audio=None,
        )
        stream = _Stream(
            [
                SimpleNamespace(
                    id="response-2",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=text_delta,
                        ),
                    ],
                ),
                SimpleNamespace(
                    id="response-2",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason="content_filter",
                            delta=empty_delta,
                        ),
                    ],
                ),
            ],
        )
        chunks = []

        with self.assertRaises(ContentBlockedError) as raised:
            async for chunk in model._parse_stream_response(
                datetime.now(),
                stream,
            ):
                chunks.append(chunk)

        self.assertListEqual(
            [chunk.content[0].text for chunk in chunks],
            ["partial"],
        )
        self.assertTrue(raised.exception.partial_output)

    async def test_openai_compatible_models_normalize_errors(self) -> None:
        """OpenAI-compatible providers normalize explicit filter codes."""
        providers = [
            (DeepSeekChatModel, "deepseek"),
            (MoonshotChatModel, "moonshot"),
            (VolcengineChatModel, "volcengine"),
        ]

        for model_class, provider in providers:
            with self.subTest(provider=provider):
                model = object.__new__(model_class)
                original = _ProviderError(
                    {
                        "code": "content_filter",
                        "message": "input blocked",
                    },
                    f"{provider}-request",
                )

                error = model._normalize_exception(original)

                self.assertIsInstance(error, ContentBlockedError)
                self.assertEqual(error.phase, ContentBlockedPhase.INPUT)
                self.assertEqual(error.provider, provider)
                self.assertEqual(error.original_code, "content_filter")
                self.assertEqual(
                    error.request_id,
                    f"{provider}-request",
                )

    async def test_anthropic_refusal_is_output_by_default(self) -> None:
        """Anthropic-compatible refusal text remains normal by default."""
        response = SimpleNamespace(
            id="response-default-refusal",
            stop_reason="refusal",
            stop_details=SimpleNamespace(explanation="policy block"),
            content=[SimpleNamespace(type="text", text="Cannot help.")],
            usage=None,
        )

        for model_class in (AnthropicChatModel, MiniMaxChatModel):
            with self.subTest(model_class=model_class):
                model = object.__new__(model_class)
                parsed = await model._parse_anthropic_completion_response(
                    datetime.now(),
                    response,
                )

                self.assertListEqual(
                    [block.text for block in parsed.content],
                    ["Cannot help."],
                )

    async def test_openai_compatible_models_map_finish_reason(self) -> None:
        """OpenAI-compatible completion filter reasons are normalized."""
        providers = [
            (DeepSeekChatModel, "deepseek"),
            (MoonshotChatModel, "moonshot"),
            (VolcengineChatModel, "volcengine"),
        ]
        response = SimpleNamespace(
            id="response-3",
            choices=[SimpleNamespace(finish_reason="content_filter")],
            usage=None,
        )

        for model_class, provider in providers:
            with self.subTest(provider=provider):
                model = object.__new__(model_class)

                with self.assertRaises(ContentBlockedError) as raised:
                    model._parse_completion_response(
                        datetime.now(),
                        response,
                    )

                self.assertEqual(
                    raised.exception.phase,
                    ContentBlockedPhase.OUTPUT,
                )
                self.assertEqual(raised.exception.provider, provider)

    async def test_anthropic_and_minimax_refusal(self) -> None:
        """Anthropic-compatible refusal reasons preserve the provider."""
        response = SimpleNamespace(
            id="response-5",
            stop_reason="refusal",
            stop_details=SimpleNamespace(explanation="policy block"),
            content=[],
            usage=None,
        )

        for model_class, provider in [
            (AnthropicChatModel, "anthropic"),
            (MiniMaxChatModel, "minimax"),
        ]:
            with self.subTest(provider=provider):
                model = object.__new__(model_class)
                model.raise_on_refusal = True

                with self.assertRaises(ContentBlockedError) as raised:
                    await model._parse_anthropic_completion_response(
                        datetime.now(),
                        response,
                    )

                self.assertDictEqual(
                    {
                        "message": raised.exception.message,
                        "phase": raised.exception.phase,
                        "provider": raised.exception.provider,
                        "original_code": raised.exception.original_code,
                    },
                    {
                        "message": "policy block",
                        "phase": ContentBlockedPhase.OUTPUT,
                        "provider": provider,
                        "original_code": "refusal",
                    },
                )

    async def test_anthropic_stream_refusal_after_output(self) -> None:
        """An Anthropic stream refusal records prior emitted output."""
        model = object.__new__(AnthropicChatModel)
        model.raise_on_refusal = True
        stream = _Stream(
            [
                SimpleNamespace(
                    type="content_block_start",
                    index=0,
                    content_block=SimpleNamespace(type="text"),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=0,
                    delta=SimpleNamespace(
                        type="text_delta",
                        text="partial",
                    ),
                ),
                SimpleNamespace(
                    type="message_delta",
                    delta=SimpleNamespace(
                        stop_reason="refusal",
                        stop_details=None,
                    ),
                    usage=None,
                ),
            ],
        )
        chunks = []

        with self.assertRaises(ContentBlockedError) as raised:
            async for chunk in (
                model._parse_anthropic_stream_completion_response(
                    datetime.now(),
                    stream,
                )
            ):
                chunks.append(chunk)

        self.assertEqual(chunks[0].content[0].text, "partial")
        self.assertTrue(raised.exception.partial_output)

    async def test_anthropic_stream_refusal_is_output_by_default(
        self,
    ) -> None:
        """Refusal stop reasons preserve streamed text by default."""
        for model_class in (AnthropicChatModel, MiniMaxChatModel):
            with self.subTest(model=model_class):
                model = object.__new__(model_class)
                stream = _Stream(
                    [
                        SimpleNamespace(
                            type="content_block_start",
                            index=0,
                            content_block=SimpleNamespace(type="text"),
                        ),
                        SimpleNamespace(
                            type="content_block_delta",
                            index=0,
                            delta=SimpleNamespace(
                                type="text_delta",
                                text="Cannot help.",
                            ),
                        ),
                        SimpleNamespace(
                            type="message_delta",
                            delta=SimpleNamespace(stop_reason="refusal"),
                            usage=None,
                        ),
                    ],
                )

                chunks = [
                    chunk
                    async for chunk in (
                        model._parse_anthropic_stream_completion_response(
                            datetime.now(),
                            stream,
                        )
                    )
                ]

                self.assertEqual(
                    [
                        block.text
                        for chunk in chunks
                        for block in chunk.content
                    ],
                    ["Cannot help."],
                )

    async def test_gemini_prompt_block(self) -> None:
        """Gemini prompt feedback maps to an input content block."""
        model = object.__new__(GeminiChatModel)
        response = SimpleNamespace(
            response_id="response-6",
            prompt_feedback=SimpleNamespace(
                block_reason="PROHIBITED_CONTENT",
                block_reason_message="prompt blocked",
            ),
            candidates=[],
            usage_metadata=None,
        )

        with self.assertRaises(ContentBlockedError) as raised:
            model._parse_completion_response(datetime.now(), response)

        self.assertEqual(raised.exception.phase, ContentBlockedPhase.INPUT)
        self.assertEqual(raised.exception.provider, "gemini")
        self.assertEqual(
            raised.exception.original_code,
            "PROHIBITED_CONTENT",
        )

    async def test_gemini_candidate_block(self) -> None:
        """Gemini safety finish reasons map to an output content block."""
        model = object.__new__(GeminiChatModel)
        response = SimpleNamespace(
            response_id="response-7",
            prompt_feedback=None,
            candidates=[
                SimpleNamespace(
                    finish_reason="SAFETY",
                    content=None,
                ),
            ],
            usage_metadata=None,
        )

        with self.assertRaises(ContentBlockedError) as raised:
            model._parse_completion_response(datetime.now(), response)

        self.assertEqual(raised.exception.phase, ContentBlockedPhase.OUTPUT)
        self.assertEqual(raised.exception.original_code, "SAFETY")

    async def test_gemini_other_reason_is_not_content_block(self) -> None:
        """Gemini's generic OTHER reason is not treated as content policy."""
        model = object.__new__(GeminiChatModel)
        response = SimpleNamespace(
            response_id="response-8",
            prompt_feedback=SimpleNamespace(block_reason="OTHER"),
            candidates=[],
            usage_metadata=None,
        )

        parsed = model._parse_completion_response(datetime.now(), response)

        self.assertListEqual(parsed.content, [])


class OpenAIResponseContentBlockTest(IsolatedAsyncioTestCase):
    """Test explicit content-block signals from the Responses API."""

    async def test_openai_response_policy_error(self) -> None:
        """A documented Responses policy code maps to an unknown phase."""
        model = object.__new__(OpenAIResponseModel)
        api_error = _ProviderError(
            {
                "code": "bio_policy",
                "message": "request policy block",
            },
            "response-request",
        )
        response = SimpleNamespace(
            id="response-9",
            incomplete_details=None,
            error=SimpleNamespace(
                code="bio_policy",
                message="policy block",
            ),
            output=[],
            usage=None,
        )

        normalized = model._normalize_exception(api_error)

        with self.assertRaises(ContentBlockedError) as raised:
            model._parse_completion_response(datetime.now(), response)

        self.assertIsInstance(normalized, ContentBlockedError)
        self.assertEqual(normalized.phase, ContentBlockedPhase.UNKNOWN)
        self.assertEqual(normalized.request_id, "response-request")
        self.assertEqual(raised.exception.message, "policy block")
        self.assertEqual(raised.exception.phase, ContentBlockedPhase.UNKNOWN)
        self.assertEqual(raised.exception.original_code, "bio_policy")

    async def test_openai_response_refusal(self) -> None:
        """A Responses refusal content part is an output block."""
        model = object.__new__(OpenAIResponseModel)
        model.raise_on_refusal = True
        response = SimpleNamespace(
            id="response-refusal",
            incomplete_details=None,
            error=None,
            output=[
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(
                            type="refusal",
                            refusal="Cannot help.",
                        ),
                    ],
                ),
            ],
            usage=None,
        )

        with self.assertRaises(ContentBlockedError) as raised:
            model._parse_completion_response(datetime.now(), response)

        self.assertEqual(raised.exception.phase, ContentBlockedPhase.OUTPUT)
        self.assertEqual(raised.exception.original_code, "refusal")
        self.assertEqual(raised.exception.message, "Cannot help.")

    async def test_openai_response_stream_refusal_after_output(self) -> None:
        """A streamed Responses refusal records prior emitted output."""
        model = object.__new__(OpenAIResponseModel)
        model.raise_on_refusal = True
        stream = _Stream(
            [
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta="partial",
                    item_id="message-1",
                    content_index=0,
                ),
                SimpleNamespace(
                    type="response.refusal.delta",
                    delta="Cannot help.",
                ),
            ],
        )
        chunks = []

        with self.assertRaises(ContentBlockedError) as raised:
            async for chunk in model._parse_stream_response(
                datetime.now(),
                stream,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks[0].content[0].text, "partial")
        self.assertEqual(raised.exception.original_code, "refusal")
        self.assertTrue(raised.exception.partial_output)

    async def test_openai_response_incomplete_after_output(self) -> None:
        """A streamed Responses filter records prior emitted output."""
        model = object.__new__(OpenAIResponseModel)
        stream = _Stream(
            [
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta="partial",
                    item_id="message-1",
                    content_index=0,
                ),
                SimpleNamespace(
                    type="response.incomplete",
                    response=SimpleNamespace(
                        id="response-10",
                        incomplete_details=SimpleNamespace(
                            reason="content_filter",
                        ),
                        error=None,
                        output=[],
                    ),
                ),
            ],
        )
        chunks = []

        with self.assertRaises(ContentBlockedError) as raised:
            async for chunk in model._parse_stream_response(
                datetime.now(),
                stream,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks[0].content[0].text, "partial")
        self.assertTrue(raised.exception.partial_output)
