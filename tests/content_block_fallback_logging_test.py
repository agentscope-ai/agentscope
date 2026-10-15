# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Verify observable content-block recovery without logging payloads."""
import asyncio
from logging import LogRecord
from unittest import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, ModelConfig
from agentscope.exception import ContentBlockedError, ContentBlockedPhase
from agentscope.message import AssistantMsg, TextBlock, UserMsg
from agentscope.middleware import (
    ContentBlockFallbackMiddleware,
    ReverseMaskStrategy,
)
from agentscope.model import ChatResponse

_DETAILS = (
    "agent=log-test, model=mock-model, provider=test, code=content_filter, "
    "request_id=request-1, phase=input"
)
_BLOCKED = f"Content-block fallback: blocked; {_DETAILS}."
_RETRY = (
    f"Content-block fallback: retrying with replacement messages "
    f"(1/3); {_DETAILS}."
)
_RECOVERED = (
    "Content-block fallback: recovered after 1 replacement call(s); "
    "agent=log-test, model=mock-model."
)


class ContentBlockFallbackLoggingTest(IsolatedAsyncioTestCase):
    """Exercise recovery lifecycle logs through real middleware calls."""

    def setUp(self) -> None:
        """Create a model and the built-in recovery middleware."""
        self.model = MockModel()
        self.middleware = ContentBlockFallbackMiddleware(
            strategy=ReverseMaskStrategy(),
            max_fallback_attempts=3,
        )
        self.agent = Agent(
            name="log-test",
            system_prompt="test",
            model=self.model,
            model_config=ModelConfig(max_retries=0),
            middlewares=[self.middleware],
        )
        self.messages = [
            AssistantMsg(name="assistant", content="private history"),
            UserMsg(name="user", content="private request"),
        ]

    @staticmethod
    def _blocked() -> ContentBlockedError:
        """Include private provider text that must not enter logs."""
        return ContentBlockedError(
            "private provider error text",
            phase=ContentBlockedPhase.INPUT,
            provider="test",
            original_code="content_filter",
            request_id="request-1",
        )

    @staticmethod
    def _response(*, is_last: bool = True) -> ChatResponse:
        """Build a successful response whose text must not enter logs."""
        return ChatResponse(
            content=[TextBlock(text="private response")],
            is_last=is_last,
        )

    def _logs(self, records: list[LogRecord]) -> list[tuple[str, str]]:
        """Extract middleware messages and check for payload leakage."""
        self.assertNotIn(
            "private",
            "\n".join(record.getMessage() for record in records),
        )
        return [
            (record.levelname, record.getMessage())
            for record in records
            if record.module == "_content_block_fallback"
        ]

    async def test_non_stream_success_logs_recovery(self) -> None:
        """Log the failed request, replacement call, and recovered result."""
        response = self._response()
        self.model.set_responses([self._blocked(), response])

        with self.assertLogs("as", level="INFO") as captured:
            result = await self.agent._call_model(
                messages=self.messages,
                tools=[],
            )

        self.assertEqual(result, response)
        self.assertEqual(
            self._logs(captured.records),
            [("WARNING", _BLOCKED), ("INFO", _RETRY), ("INFO", _RECOVERED)],
        )

    async def test_exhausted_budget_is_logged(self) -> None:
        """Log exhaustion even when recovery is disabled with a zero budget."""
        for budget in (0, 1):
            with self.subTest(budget=budget):
                self.middleware.max_fallback_attempts = budget
                self.model.set_responses(
                    [self._blocked() for _ in range(budget + 1)],
                )
                with self.assertLogs("as", level="INFO") as captured:
                    with self.assertRaises(ContentBlockedError):
                        await self.agent._call_model(
                            messages=self.messages,
                            tools=[],
                        )

                expected = [("WARNING", _BLOCKED)]
                if budget:
                    expected.extend(
                        [
                            (
                                "INFO",
                                f"Content-block fallback: retrying with "
                                f"replacement messages (1/1); {_DETAILS}.",
                            ),
                            ("WARNING", _BLOCKED),
                        ],
                    )
                expected.append(
                    (
                        "WARNING",
                        f"Content-block fallback: budget exhausted "
                        f"({budget}/{budget}); {_DETAILS}.",
                    ),
                )
                self.assertEqual(self._logs(captured.records), expected)

    async def test_strategy_stop_is_logged(self) -> None:
        """Distinguish a strategy declining recovery from budget exhaustion."""
        self.model.set_responses([self._blocked()])
        with self.assertLogs("as", level="INFO") as captured:
            with self.assertRaises(ContentBlockedError):
                await self.agent._call_model(
                    messages=[self.messages[-1]],
                    tools=[],
                )

        self.assertEqual(
            self._logs(captured.records),
            [
                ("WARNING", _BLOCKED),
                (
                    "WARNING",
                    f"Content-block fallback: strategy stopped at attempt "
                    f"1/3; {_DETAILS}.",
                ),
            ],
        )

    async def test_stream_success_waits_until_consumed(self) -> None:
        """Creating a replacement stream does not yet mean recovery worked."""
        for initial in (self._blocked(), [self._blocked()]):
            with self.subTest(initial=type(initial)):
                await self.middleware.clear_quarantine(self.agent)
                self.model.set_responses(
                    [initial, [self._response(is_last=False)]],
                )
                with self.assertLogs("as", level="INFO") as captured:
                    stream = await self.agent._call_model(
                        messages=self.messages,
                        tools=[],
                    )
                    await anext(stream)
                    self.assertEqual(self.agent.state.middle_context, {})
                    self.assertEqual(
                        self._logs(captured.records),
                        [("WARNING", _BLOCKED), ("INFO", _RETRY)],
                    )
                    async for _ in stream:
                        pass

                self.assertEqual(
                    self._logs(captured.records),
                    [
                        ("WARNING", _BLOCKED),
                        ("INFO", _RETRY),
                        ("INFO", _RECOVERED),
                    ],
                )
                self.assertEqual(
                    self.agent.state.middle_context,
                    {
                        "ContentBlockFallbackMiddleware": {
                            "quarantined_blocks": {
                                self.messages[0].id: [
                                    self.messages[0].content[0].id,
                                ],
                            },
                        },
                    },
                )

    async def test_stream_to_non_stream_recovery_is_logged(self) -> None:
        """Log recovery when a failed stream is replaced by a full result."""
        response = self._response()
        self.model.set_responses([[self._blocked()], response])
        with self.assertLogs("as", level="INFO") as captured:
            stream = await self.agent._call_model(
                messages=self.messages,
                tools=[],
            )
            results = [result async for result in stream]

        self.assertEqual(results, [response])
        self.assertEqual(
            self._logs(captured.records),
            [("WARNING", _BLOCKED), ("INFO", _RETRY), ("INFO", _RECOVERED)],
        )

    async def test_interrupted_recovery_does_not_log_success(self) -> None:
        """Cancellation and early stream closure do not log success."""
        for mode in ("close", "stream_cancel", "call_cancel"):
            with self.subTest(mode=mode):
                replacement = (
                    asyncio.CancelledError()
                    if mode == "call_cancel"
                    else [
                        self._response(is_last=False),
                        asyncio.CancelledError(),
                    ]
                )
                self.model.set_responses([self._blocked(), replacement])
                with self.assertLogs("as", level="INFO") as captured:
                    result = await self.agent._call_model(
                        messages=self.messages,
                        tools=[],
                    )
                    if mode == "close":
                        await anext(result)
                        await result.aclose()
                    elif mode == "stream_cancel":
                        async for _ in result:
                            pass

                self.assertEqual(
                    self._logs(captured.records),
                    [("WARNING", _BLOCKED), ("INFO", _RETRY)],
                )

    async def test_partial_output_block_logs_stop_without_success(
        self,
    ) -> None:
        """A later block cannot turn partial output into recovery success."""
        self.model.set_responses(
            [
                self._blocked(),
                [self._response(is_last=False), self._blocked()],
            ],
        )
        with self.assertLogs("as", level="INFO") as captured:
            stream = await self.agent._call_model(
                messages=self.messages,
                tools=[],
            )
            with self.assertRaises(ContentBlockedError):
                async for _ in stream:
                    pass

        self.assertEqual(
            self._logs(captured.records),
            [
                ("WARNING", _BLOCKED),
                ("INFO", _RETRY),
                (
                    "WARNING",
                    "Content-block fallback: stopped after partial output; "
                    "agent=log-test, model=mock-model, provider=test, "
                    "code=content_filter, request_id=request-1.",
                ),
            ],
        )

    async def test_ordinary_success_has_no_fallback_logs(self) -> None:
        """Avoid announcing recovery for calls that were never blocked."""
        for response in (self._response(), [self._response(is_last=False)]):
            with self.subTest(stream=isinstance(response, list)):
                self.model.set_responses([response])
                with self.assertNoLogs("as", level="INFO"):
                    result = await self.agent._call_model(
                        messages=self.messages,
                        tools=[],
                    )
                    if isinstance(response, list):
                        async for _ in result:
                            pass
