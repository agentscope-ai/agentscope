# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for provider content-block normalization."""
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import httpx
import openai
from pydantic import BaseModel

from agentscope.credential import (
    AnthropicCredential,
    DashScopeCredential,
    DeepSeekCredential,
    GeminiCredential,
    MiniMaxCredential,
    MoonshotCredential,
    OpenAICredential,
    VolcengineCredential,
)
from agentscope.exception import ContentBlockedError, ContentBlockedPhase
from agentscope.message import TextBlock, UserMsg
from agentscope.model import (
    AnthropicChatModel,
    ChatResponse,
    DashScopeChatModel,
    DeepSeekChatModel,
    GeminiChatModel,
    MiniMaxChatModel,
    MoonshotChatModel,
    OpenAIChatModel,
    OpenAIResponseModel,
    StructuredResponse,
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


def _model_with_filters(model_class: type) -> Any:
    """Create a parser-only model with explicit filter opt-in."""
    model = object.__new__(model_class)
    model.raise_on_content_filter = True
    model.raise_on_refusal = False
    return model


class ContentBlockCompatibilityTest(IsolatedAsyncioTestCase):
    """Verify opt-in filtering and backward-compatible default behavior."""

    def test_sdk_normalization_switch_and_provider_fields(self) -> None:
        """Known provider payloads normalize only with explicit opt-in."""
        cases: tuple[
            tuple[type, str, dict[str, Any], ContentBlockedPhase],
            ...,
        ] = (
            (
                OpenAIChatModel,
                "openai",
                {"code": "content_filter"},
                ContentBlockedPhase.INPUT,
            ),
            (
                DashScopeChatModel,
                "dashscope",
                {"code": "DataInspectionFailed"},
                ContentBlockedPhase.UNKNOWN,
            ),
            (
                MoonshotChatModel,
                "moonshot",
                {"code": 400, "type": "content_filter"},
                ContentBlockedPhase.UNKNOWN,
            ),
            (
                VolcengineChatModel,
                "volcengine",
                {"code": "OutputTextSensitiveContentDetected"},
                ContentBlockedPhase.OUTPUT,
            ),
            (
                OpenAIResponseModel,
                "openai",
                {"code": "bio_policy"},
                ContentBlockedPhase.UNKNOWN,
            ),
            (
                OpenAIResponseModel,
                "openai",
                {"code": "misalignment_policy_violation"},
                ContentBlockedPhase.UNKNOWN,
            ),
        )
        for model_class, provider, fields, phase in cases:
            for enabled in (False, True):
                with self.subTest(model=model_class, enabled=enabled):
                    model = _model_with_filters(model_class)
                    model.raise_on_content_filter = enabled
                    original = _ProviderError(
                        {
                            "message": "outer",
                            "error": {**fields, "message": "inner"},
                        },
                        "request-id",
                    )
                    normalized = model._normalize_exception(original)
                    if not enabled:
                        self.assertIs(normalized, original)
                        continue
                    code = fields.get("type", fields["code"])
                    self.assertEqual(
                        {
                            "message": normalized.message,
                            "provider": normalized.provider,
                            "phase": normalized.phase,
                            "original_code": normalized.original_code,
                            "request_id": normalized.request_id,
                            "partial_output": normalized.partial_output,
                            "requires_review": normalized.requires_review,
                        },
                        {
                            "message": "inner",
                            "provider": provider,
                            "phase": phase,
                            "original_code": code,
                            "request_id": "request-id",
                            "partial_output": False,
                            "requires_review": code
                            in {
                                "bio_policy",
                                "misalignment_policy_violation",
                            },
                        },
                    )

    async def test_stream_refusal_uses_stable_diagnostic(self) -> None:
        """Do not present the first refusal fragment as a full explanation."""
        for model_class, response, expected in (
            (
                OpenAIChatModel,
                SimpleNamespace(
                    id="refusal",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(refusal="I"),
                        ),
                    ],
                ),
                "openai refused the response.",
            ),
            (
                OpenAIResponseModel,
                SimpleNamespace(type="response.refusal.delta", delta="I"),
                "OpenAI refused to generate the response.",
            ),
        ):
            with self.subTest(model=model_class):
                model = _model_with_filters(model_class)
                model.raise_on_content_filter = False
                model.raise_on_refusal = True
                with self.assertRaises(ContentBlockedError) as caught:
                    async for _ in model._parse_stream_response(
                        datetime.now(),
                        _Stream([response]),
                    ):
                        pass
                self.assertEqual(
                    (
                        caught.exception.message,
                        caught.exception.partial_output,
                    ),
                    (expected, False),
                )

    def test_content_filter_constructor_defaults_and_opt_in(self) -> None:
        """All filter-aware adapters require an explicit opt-in."""
        providers = (
            (OpenAIChatModel, OpenAICredential, "openai.AsyncClient"),
            (OpenAIResponseModel, OpenAICredential, "openai.AsyncClient"),
            (DashScopeChatModel, DashScopeCredential, "openai.AsyncClient"),
            (DeepSeekChatModel, DeepSeekCredential, "openai.AsyncClient"),
            (MoonshotChatModel, MoonshotCredential, "openai.AsyncClient"),
            (VolcengineChatModel, VolcengineCredential, "openai.AsyncClient"),
            (GeminiChatModel, GeminiCredential, "google.genai.Client"),
        )
        for model_class, credential_class, client_path in providers:
            for options in ({}, {"raise_on_content_filter": True}):
                with self.subTest(model=model_class, options=options):
                    with patch(client_path):
                        model = model_class(
                            credential=credential_class(api_key="test"),
                            model="test-model",
                            **options,
                        )
                    self.assertEqual(
                        model.raise_on_content_filter,
                        options.get("raise_on_content_filter", False),
                    )

    async def test_default_chat_filter_retains_same_chunk_content(
        self,
    ) -> None:
        """Legacy parsing retains content accompanying a filter signal."""
        fields = {
            "content": "partial content",
            "refusal": None,
            "reasoning_content": None,
            "reasoning": None,
            "audio": None,
            "tool_calls": None,
        }
        for model_class in (
            OpenAIChatModel,
            DashScopeChatModel,
            DeepSeekChatModel,
            MoonshotChatModel,
            VolcengineChatModel,
        ):
            for streaming in (False, True):
                for enabled in (False, True):
                    with self.subTest(
                        model=model_class,
                        streaming=streaming,
                        enabled=enabled,
                    ):
                        model = _model_with_filters(model_class)
                        model.raise_on_content_filter = enabled
                        choice = SimpleNamespace(
                            finish_reason="content_filter",
                            message=SimpleNamespace(**fields),
                            delta=SimpleNamespace(**fields),
                        )
                        response = SimpleNamespace(
                            id="filtered",
                            choices=[choice],
                            usage=None,
                        )

                        async def parse(
                            model: Any = model,
                            response: Any = response,
                            streaming: bool = streaming,
                        ) -> list:
                            if streaming:
                                stream = model._parse_stream_response(
                                    datetime.now(),
                                    _Stream([response]),
                                )
                                return [chunk async for chunk in stream]
                            return [
                                model._parse_completion_response(
                                    datetime.now(),
                                    response,
                                ),
                            ]

                        if enabled:
                            with self.assertRaises(
                                ContentBlockedError,
                            ) as caught:
                                await parse()
                            self.assertEqual(
                                caught.exception.partial_output,
                                False,
                            )
                        else:
                            parsed = await parse()
                            self.assertEqual(
                                [b.text for r in parsed for b in r.content],
                                ["partial content"],
                            )

    async def test_disabled_gemini_filter_skips_inspection(self) -> None:
        """The disabled switch avoids inspecting filter signals entirely."""
        model = _model_with_filters(GeminiChatModel)
        model.raise_on_content_filter = False
        response = SimpleNamespace(
            response_id="gemini-filter",
            candidates=[],
            usage_metadata=None,
        )
        with patch(
            "agentscope.model._gemini._model._gemini_content_block_error",
        ) as inspect_block:
            parsed = model._parse_completion_response(datetime.now(), response)
            chunks = [
                chunk
                async for chunk in model._parse_stream_response(
                    datetime.now(),
                    _Stream([response]),
                )
            ]
        self.assertEqual((parsed.content, chunks), ([], []))
        inspect_block.assert_not_called()

    async def test_default_gemini_and_responses_filters_are_not_errors(
        self,
    ) -> None:
        """Default mode preserves incomplete and safety-blocked responses."""
        model = _model_with_filters(GeminiChatModel)
        model.raise_on_content_filter = False
        for reason in (
            "SAFETY",
            "BLOCKLIST",
            "PROHIBITED_CONTENT",
            "SPII",
            "IMAGE_SAFETY",
        ):
            with self.subTest(reason=reason):
                response = SimpleNamespace(
                    response_id="gemini-filter",
                    prompt_feedback=SimpleNamespace(block_reason=reason),
                    candidates=[],
                    usage_metadata=None,
                )
                parsed = model._parse_completion_response(
                    datetime.now(),
                    response,
                )
                self.assertEqual(parsed.content, [])
                chunks = [
                    c
                    async for c in model._parse_stream_response(
                        datetime.now(),
                        _Stream([response]),
                    )
                ]
                self.assertEqual(chunks, [])

        model = _model_with_filters(OpenAIResponseModel)
        model.raise_on_content_filter = False
        response = SimpleNamespace(
            id="filtered-response",
            incomplete_details=SimpleNamespace(reason="content_filter"),
            error=None,
            output=[
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(type="output_text", text="partial"),
                    ],
                ),
            ],
            usage=None,
        )
        parsed = model._parse_completion_response(datetime.now(), response)
        self.assertEqual([b.text for b in parsed.content], ["partial"])
        chunks = [
            c
            async for c in model._parse_stream_response(
                datetime.now(),
                _Stream(
                    [
                        SimpleNamespace(
                            type="response.output_text.delta",
                            delta="partial",
                            item_id="message-1",
                            content_index=0,
                        ),
                        SimpleNamespace(
                            type="response.incomplete",
                            response=response,
                        ),
                    ],
                ),
            )
        ]
        self.assertEqual(
            [b.text for c in chunks for b in c.content],
            ["partial"],
        )

    async def test_dashscope_structured_output_opt_in(self) -> None:
        """Disabled normalization preserves legacy strategy fallback."""
        messages = [UserMsg(name="user", content="Return an empty object.")]
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                with patch("openai.AsyncClient"):
                    model = DashScopeChatModel(
                        credential=DashScopeCredential(api_key="test"),
                        model="test",
                        max_retries=0,
                        raise_on_content_filter=enabled,
                    )
                original = openai.BadRequestError(
                    "inspection failed",
                    response=httpx.Response(
                        400,
                        request=httpx.Request("POST", "https://example.com"),
                    ),
                    body={"code": "DataInspectionFailed"},
                )
                expected = StructuredResponse(content={})
                with patch.object(
                    model,
                    "_call_api_with_structured_output",
                    new=AsyncMock(side_effect=[original, expected]),
                ) as call:
                    if enabled:
                        with self.assertRaises(ContentBlockedError) as caught:
                            await model.generate_structured_output(
                                messages,
                                BaseModel,
                            )
                        self.assertIs(caught.exception.__cause__, original)
                        self.assertEqual(call.await_count, 1)
                    else:
                        result = await model.generate_structured_output(
                            messages,
                            BaseModel,
                        )
                        self.assertEqual(
                            (result, call.await_count),
                            (expected, 2),
                        )

    async def test_sdk_stream_error_tracks_visible_output(self) -> None:
        """SDK errors track emitted chunks without middleware."""
        for visible in (False, True):
            for enabled in (False, True):
                with self.subTest(visible=visible, enabled=enabled):
                    with patch("openai.AsyncClient"):
                        model = DashScopeChatModel(
                            credential=DashScopeCredential(api_key="test"),
                            model="test",
                            raise_on_content_filter=enabled,
                        )
                    original = _ProviderError(
                        {"code": "DataInspectionFailed", "message": "blocked"},
                        "request-1",
                    )

                    async def stream(
                        visible: bool = visible,
                        error: Exception = original,
                    ) -> Any:
                        yield ChatResponse(
                            content=[TextBlock(text="partial")]
                            if visible
                            else [],
                            is_last=False,
                        )
                        raise error

                    with patch.object(
                        model,
                        "_call_api",
                        return_value=stream(),
                    ):
                        result = await model(messages=[])
                        chunks = []
                        with self.assertRaises(
                            ContentBlockedError if enabled else _ProviderError,
                        ) as caught:
                            async for chunk in result:
                                chunks.append(chunk)
                    self.assertEqual(
                        [b.text for c in chunks for b in c.content],
                        ["partial"] if visible else [],
                    )
                    if enabled:
                        self.assertEqual(
                            (
                                caught.exception.partial_output,
                                caught.exception.__cause__,
                            ),
                            (visible, original),
                        )
                    else:
                        self.assertIs(caught.exception, original)

    def test_unverified_error_codes_remain_sdk_errors(self) -> None:
        """Do not infer exception codes from compatible finish reasons."""
        for model_class, body in (
            (DeepSeekChatModel, {"code": "content_filter"}),
            (OpenAIResponseModel, {"code": "content_filter"}),
            (MoonshotChatModel, {"code": "content_filter"}),
            (VolcengineChatModel, {"code": "content_filter"}),
            (
                VolcengineChatModel,
                {"code": "InputTextSensitiveContentDetected.Unknown"},
            ),
        ):
            with self.subTest(model=model_class, body=body):
                model = _model_with_filters(model_class)
                error = _ProviderError(
                    {**body, "message": "blocked"},
                    "request",
                )
                self.assertIs(model._normalize_exception(error), error)


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
        model = _model_with_filters(DashScopeChatModel)
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
        model = _model_with_filters(OpenAIChatModel)
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
        model = _model_with_filters(OpenAIChatModel)
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
        model = _model_with_filters(OpenAIChatModel)
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
        model = _model_with_filters(OpenAIChatModel)
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
        model = _model_with_filters(OpenAIChatModel)
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
        model = _model_with_filters(OpenAIChatModel)
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
        model = _model_with_filters(DashScopeChatModel)
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
            (
                MoonshotChatModel,
                "moonshot",
                "type",
                "content_filter",
                ContentBlockedPhase.UNKNOWN,
            ),
            (
                VolcengineChatModel,
                "volcengine",
                "code",
                "InputTextSensitiveContentDetected",
                ContentBlockedPhase.INPUT,
            ),
            (
                VolcengineChatModel,
                "volcengine",
                "code",
                "OutputTextSensitiveContentDetected",
                ContentBlockedPhase.OUTPUT,
            ),
        ]

        for model_class, provider, field, code, phase in providers:
            with self.subTest(provider=provider):
                model = _model_with_filters(model_class)
                original = _ProviderError(
                    {
                        field: code,
                        "message": "input blocked",
                    },
                    f"{provider}-request",
                )

                error = model._normalize_exception(original)

                self.assertIsInstance(error, ContentBlockedError)
                self.assertEqual(error.phase, phase)
                self.assertEqual(error.provider, provider)
                self.assertEqual(error.original_code, code)
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
                model = _model_with_filters(model_class)
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
                model = _model_with_filters(model_class)

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
                model = _model_with_filters(model_class)
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
        model = _model_with_filters(AnthropicChatModel)
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
                model = _model_with_filters(model_class)
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
        model = _model_with_filters(GeminiChatModel)
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
        model = _model_with_filters(GeminiChatModel)
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
        model = _model_with_filters(GeminiChatModel)
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

    async def test_review_flags_cover_all_policy_error_paths(self) -> None:
        """The adapter assigns review flags for SDK and response errors."""
        model = _model_with_filters(OpenAIResponseModel)
        for code, requires_review in (
            ("bio_policy", True),
            ("MISALIGNMENT_POLICY_VIOLATION", True),
            ("image_content_policy_violation", False),
        ):
            with self.subTest(code=code):
                fields = {"code": code, "message": "policy block"}
                response = SimpleNamespace(
                    id="response-policy",
                    incomplete_details=None,
                    error=SimpleNamespace(**fields),
                    output=[],
                    usage=None,
                )
                errors = [
                    model._normalize_exception(
                        _ProviderError(fields, "response-policy"),
                    ),
                ]
                with self.assertRaises(ContentBlockedError) as caught:
                    model._parse_completion_response(datetime.now(), response)
                errors.append(caught.exception)
                for event in (
                    SimpleNamespace(type="response.failed", response=response),
                    SimpleNamespace(type="error", **fields),
                ):
                    with patch(
                        "agentscope.model._openai_response._model."
                        "_generate_id",
                        return_value="response-policy",
                    ):
                        with self.assertRaises(ContentBlockedError) as caught:
                            async for _ in model._parse_stream_response(
                                datetime.now(),
                                _Stream([event]),
                            ):
                                pass
                    errors.append(caught.exception)
                expected = {
                    "message": "policy block",
                    "phase": ContentBlockedPhase.UNKNOWN,
                    "provider": "openai",
                    "original_code": code.casefold(),
                    "request_id": "response-policy",
                    "partial_output": False,
                    "requires_review": requires_review,
                }
                self.assertEqual(
                    [vars(error) for error in errors],
                    [{**expected, "original_code": code}, *[expected] * 3],
                )

    async def test_openai_response_policy_error(self) -> None:
        """A documented Responses policy code maps to an unknown phase."""
        model = _model_with_filters(OpenAIResponseModel)
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
        model = _model_with_filters(OpenAIResponseModel)
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
        model = _model_with_filters(OpenAIResponseModel)
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
        model = _model_with_filters(OpenAIResponseModel)
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
