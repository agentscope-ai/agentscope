# -*- coding: utf-8 -*-
"""Structured-output attempt accounting through the real OpenAI SDK.

Only HTTP is simulated; formatting, response parsing, validation and fallback
remain real. Regression coverage for #3246.
"""
import asyncio
import json
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import httpx
import openai
from pydantic import BaseModel

from agentscope.credential import OpenAICredential
from agentscope.exception import StructuredOutputError
from agentscope.message import ToolCallBlock, UserMsg
from agentscope.model import ChatResponse, ChatUsage, OpenAIChatModel
from agentscope.tool import ToolChoice


class _Output(BaseModel):
    """A deliberately small structured result."""

    answer: str


def _response(
    number: int,
    outcome: str,
    stream: bool,
) -> httpx.Response:
    """Build a complete HTTP response, including a trailing stream usage."""
    usage = {
        "prompt_tokens": number * 100,
        "completion_tokens": number * 10,
        "total_tokens": number * 110,
        "prompt_tokens_details": {"cached_tokens": number * 5},
    }
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "plain text" if outcome == "plain" else None,
    }
    if outcome != "plain":
        message["tool_calls"] = [
            {
                "id": f"call-{number}",
                "type": "function",
                "function": {
                    "name": "generate_structured_output",
                    "arguments": (
                        '{"wrong_field":123}'
                        if outcome == "schema"
                        else '{"answer":"ok"}'
                    ),
                },
            },
        ]
    base = {"id": f"response-{number}", "created": 0, "model": "offline"}
    reason = "stop" if outcome == "plain" else "tool_calls"
    if not stream:
        return httpx.Response(
            200,
            json={
                **base,
                "object": "chat.completion",
                "usage": usage if outcome != "missing" else None,
                "choices": [
                    {"index": 0, "message": message, "finish_reason": reason},
                ],
            },
        )
    if "tool_calls" in message:
        message["tool_calls"][0]["index"] = 0
    chunks = [
        {
            **base,
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": message, "finish_reason": None},
            ],
        },
        {
            **base,
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": reason},
            ],
        },
        {
            **base,
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": usage if outcome != "missing" else None,
        },
    ]
    payload = "".join(
        "data: " + json.dumps(chunk) + "\n\n" for chunk in chunks
    )
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=payload + "data: [DONE]\n\n",
    )


class StructuredUsageTest(IsolatedAsyncioTestCase):
    """Observe known attempt usage without changing response semantics."""

    def make_model(
        self,
        outcomes: list[str],
        stream: bool,
    ) -> tuple[OpenAIChatModel, list[dict]]:
        """Create an offline transport and register deterministic cleanup."""
        requests: list[dict] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            number = len(requests)
            return _response(number, outcomes[number - 1], stream)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAIChatModel(
            credential=OpenAICredential(
                api_key="offline-unused",
                base_url="https://example.invalid/v1",
            ),
            model="offline",
            stream=stream,
            max_retries=0,
            retry_delay=0,
            client_kwargs={"http_client": client, "max_retries": 0},
        )
        self.addAsyncCleanup(model.client.close)
        return model, requests

    async def test_fallback_and_final_failure_preserve_attempt_usage(
        self,
    ) -> None:
        """Report failed validation attempts in both transport modes."""
        for stream in (False, True):
            for invalid in ("plain", "schema"):
                for fail in (False, True):
                    with self.subTest(
                        stream=stream,
                        invalid=invalid,
                        fail=fail,
                    ):
                        outcomes = (
                            [invalid] * 3 if fail else [invalid, "valid"]
                        )
                        model, requests = self.make_model(outcomes, stream)
                        records: list[ChatUsage] = []
                        call = model.generate_structured_output(
                            [UserMsg("user", "synthetic")],
                            _Output,
                            on_usage=records.append,
                        )
                        if fail:
                            with self.assertRaises(StructuredOutputError):
                                await call
                        else:
                            result = await call
                            self.assertEqual(result.content, {"answer": "ok"})
                            self.assertEqual(result.usage.input_tokens, 200)
                            self.assertIsNot(result.usage, records[-1])
                        self.assertEqual(len(requests), len(outcomes))
                        self.assertEqual(
                            [
                                (
                                    usage.input_tokens,
                                    usage.output_tokens,
                                    usage.cache_input_tokens,
                                )
                                for usage in records
                            ],
                            [
                                (n * 100, n * 10, n * 5)
                                for n in range(1, len(outcomes) + 1)
                            ],
                        )
                        self.assertTrue(
                            all("on_usage" not in r for r in requests),
                        )

    async def test_first_success_and_missing_usage(self) -> None:
        """Unknown usage is not synthesized into a zero-token record."""
        for stream in (False, True):
            for outcome in ("valid", "missing"):
                with self.subTest(stream=stream, outcome=outcome):
                    model, requests = self.make_model([outcome], stream)
                    records: list[ChatUsage] = []
                    result = await model.generate_structured_output(
                        [UserMsg("user", "synthetic")],
                        _Output,
                        on_usage=records.append,
                    )
                    self.assertEqual(len(requests), 1)
                    if outcome == "missing":
                        self.assertEqual(records, [])
                        self.assertIsNone(result.usage)
                    else:
                        self.assertEqual(len(records), 1)
                        self.assertEqual(records[0].input_tokens, 100)
                        self.assertEqual(result.usage.input_tokens, 100)

    async def test_without_observer_keeps_existing_result(self) -> None:
        """The opt-in observer does not change the default fallback path."""
        model, requests = self.make_model(["schema", "valid"], False)
        result = await model.generate_structured_output(
            [UserMsg("user", "synthetic")],
            _Output,
        )
        self.assertEqual(len(requests), 2)
        self.assertEqual(result.usage.input_tokens, 200)

    async def test_observer_errors_never_retry_the_model(self) -> None:
        """Observer errors must not trigger model fallback or retries."""
        response = httpx.Response(
            429,
            request=httpx.Request("POST", "https://example.invalid"),
        )
        errors = [
            StructuredOutputError("observer failed"),
            openai.RateLimitError(
                "observer failed",
                response=response,
                body={},
            ),
        ]
        for outcome in ("schema", "valid"):
            for error in errors:
                with self.subTest(outcome=outcome, error=type(error).__name__):
                    model, requests = self.make_model([outcome], False)
                    model.max_retries = 2

                    def observe(
                        usage: ChatUsage,
                        observer_error: Exception = error,
                    ) -> None:
                        self.assertEqual(usage.input_tokens, 100)
                        raise observer_error

                    with self.assertRaises(type(error)) as raised:
                        await model.generate_structured_output(
                            [UserMsg("user", "synthetic")],
                            _Output,
                            on_usage=observe,
                        )
                    self.assertIs(raised.exception, error)
                    self.assertEqual(len(requests), 1)

    async def test_explicit_choice_reports_failure_without_fallback(
        self,
    ) -> None:
        """Explicit tool choice keeps the single-strategy contract."""
        model, requests = self.make_model(["schema"], False)
        records: list[ChatUsage] = []
        with self.assertRaises(StructuredOutputError):
            await model.generate_structured_output(
                [UserMsg("user", "synthetic")],
                _Output,
                tool_choice=ToolChoice(mode="auto"),
                on_usage=records.append,
            )
        self.assertEqual(len(requests), 1)
        self.assertEqual(records[0].input_tokens, 100)

    async def test_observer_receives_detached_usage(self) -> None:
        """An observer cannot change usage on the returned response."""
        model, _ = self.make_model(["valid"], False)

        def observe(usage: ChatUsage) -> None:
            usage.input_tokens = 999
            usage.cache_input_tokens = 999
            usage.metadata = {"recorded": True}

        result = await model.generate_structured_output(
            [UserMsg("user", "synthetic")],
            _Output,
            on_usage=observe,
        )
        self.assertEqual(result.usage.input_tokens, 100)
        self.assertEqual(result.usage.cache_input_tokens, 5)
        self.assertIsNone(result.usage.metadata)

    async def test_partial_stream_usage_survives_error_and_cancellation(
        self,
    ) -> None:
        """Report the last snapshot once, including unfinished streams."""
        for error in (RuntimeError("stream failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                model, requests = self.make_model([], True)
                records: list[ChatUsage] = []
                usage = ChatUsage(
                    100,
                    10,
                    0.1,
                    cache_input_tokens=5,
                    metadata={"details": {"cached_tokens": 5}},
                )

                async def chunks(
                    snapshot: ChatUsage = usage,
                    failure: BaseException = error,
                ) -> AsyncGenerator[ChatResponse, None]:
                    yield ChatResponse(
                        content=[],
                        is_last=False,
                        usage=snapshot,
                    )
                    snapshot.output_tokens = 20
                    yield ChatResponse(
                        content=[],
                        is_last=False,
                        usage=snapshot,
                    )
                    raise failure

                with patch.object(model, "_call_api", return_value=chunks()):
                    with self.assertRaises(type(error)) as raised:
                        await model.generate_structured_output(
                            [UserMsg("user", "synthetic")],
                            _Output,
                            on_usage=records.append,
                        )
                self.assertIs(raised.exception, error)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].output_tokens, 20)
                self.assertIsNot(records[0], usage)
                records[0].metadata["details"]["cached_tokens"] = 999
                self.assertEqual(usage.metadata["details"]["cached_tokens"], 5)
                self.assertEqual(requests, [])

    async def test_concurrent_calls_do_not_share_observers(self) -> None:
        """Overlapping invocations on one model retain their own usage."""
        model, _ = self.make_model([], False)
        both_started = asyncio.Event()
        started = 0

        async def call_api(**kwargs: Any) -> ChatResponse:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)
            name = kwargs["messages"][0].name
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="call-1",
                        name="generate_structured_output",
                        input='{"answer":"ok"}',
                    ),
                ],
                is_last=True,
                usage=ChatUsage(100 if name == "one" else 200, 10, 0.1),
            )

        one: list[ChatUsage] = []
        two: list[ChatUsage] = []
        with patch.object(model, "_call_api", side_effect=call_api):
            await asyncio.gather(
                model.generate_structured_output(
                    [UserMsg("one", "synthetic")],
                    _Output,
                    on_usage=one.append,
                ),
                model.generate_structured_output(
                    [UserMsg("two", "synthetic")],
                    _Output,
                    on_usage=two.append,
                ),
            )
        self.assertEqual([u.input_tokens for u in one], [100])
        self.assertEqual([u.input_tokens for u in two], [200])

    async def test_async_observer_is_rejected_before_request(self) -> None:
        """Avoid silently dropping accounting in an unawaited coroutine."""
        model, requests = self.make_model([], False)

        async def observe(usage: ChatUsage) -> None:
            raise AssertionError("must not be invoked")

        with self.assertRaisesRegex(TypeError, "synchronous callable"):
            await model.generate_structured_output(
                [UserMsg("user", "synthetic")],
                _Output,
                on_usage=observe,  # type: ignore[arg-type]
            )
        self.assertEqual(requests, [])
