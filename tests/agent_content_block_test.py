# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for content-block errors without recovery middleware."""
from copy import deepcopy
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig, ModelConfig
from agentscope.exception import ContentBlockedError, ContentBlockedPhase
from agentscope.message import Msg, TextBlock, UserMsg
from agentscope.model import ChatResponse


class _TrackingModel(MockModel):
    """Record model inputs to verify unchanged request retries."""

    def __init__(self) -> None:
        """Initialize the recorded model inputs."""
        super().__init__()
        self.received_messages: list[list[Msg]] = []

    async def _call_api(self, *args: Any, **kwargs: Any) -> Any:
        """Record the input before returning a deterministic response."""
        self.received_messages.append(deepcopy(kwargs["messages"]))
        return await super()._call_api(*args, **kwargs)


class _NormalizingModel(MockModel):
    """Translate simulated SDK errors into normalized input blocks."""

    def _normalize_exception(self, error: Exception) -> Exception:
        """Return a content block with the original error's message."""
        return ContentBlockedError(
            f"{error}",
            phase=ContentBlockedPhase.INPUT,
        )


def _response(text: str, *, is_last: bool = True) -> ChatResponse:
    """Build a deterministic response for whole and streamed calls."""
    return ChatResponse(
        content=[TextBlock(id="text-1", text=text)],
        is_last=is_last,
    )


class AgentContentBlockTest(IsolatedAsyncioTestCase):
    """Verify phase-aware retry, stream observation, and error identity."""

    def _agent(self, model: MockModel, *, retries: int = 0) -> Agent:
        """Build an agent without any middleware."""
        return Agent(
            name="test-agent",
            system_prompt="test",
            model=model,
            model_config=ModelConfig(max_retries=retries),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    def test_error_exposes_normalized_fields(self) -> None:
        """Errors expose the complete provider-independent structure."""
        error = ContentBlockedError(
            "blocked",
            phase=ContentBlockedPhase.INPUT,
            provider="test",
            original_code="blocked_input",
            request_id="request-1",
            partial_output=True,
        )
        self.assertEqual(
            vars(error),
            {
                "message": "blocked",
                "phase": ContentBlockedPhase.INPUT,
                "provider": "test",
                "original_code": "blocked_input",
                "request_id": "request-1",
                "partial_output": True,
                "requires_review": False,
            },
        )

    def test_review_flag_is_not_inferred_from_provider_codes(self) -> None:
        """Only explicit adapter decisions set the provider-neutral flag."""
        for provider, code in (
            ("openai", "bio_policy"),
            ("openai", "misalignment_policy_violation"),
            ("custom", "review_required"),
        ):
            with self.subTest(provider=provider, code=code):
                default = ContentBlockedError(
                    "blocked",
                    provider=provider,
                    original_code=code,
                )
                explicit = ContentBlockedError(
                    "blocked",
                    provider=provider,
                    original_code=code,
                    requires_review=True,
                )
                self.assertEqual(
                    (default.requires_review, explicit.requires_review),
                    (False, True),
                )

    async def test_model_normalization_preserves_cause(self) -> None:
        """Both immediate and streamed SDK errors preserve their cause."""
        for streamed in (False, True):
            with self.subTest(streamed=streamed):
                original = RuntimeError("provider error")
                model = _NormalizingModel()
                if streamed:
                    model.set_responses([[original]])
                else:
                    model.set_responses([original])
                with self.assertRaises(ContentBlockedError) as caught:
                    result = await model(messages=[])
                    if streamed:
                        async for _ in result:
                            pass
                self.assertEqual(
                    (caught.exception.__cause__, model.cnt),
                    (original, 1),
                )

    async def test_phase_controls_same_payload_retries(self) -> None:
        """Only OUTPUT blocks can resample an unchanged request."""
        for phase in ContentBlockedPhase:
            with self.subTest(phase=phase):
                error = ContentBlockedError("blocked", phase=phase)
                model = _TrackingModel()
                success = _response("success")
                model.set_responses([error, success])
                agent = self._agent(model, retries=1)
                messages = [UserMsg(name="user", content="request")]
                if phase == ContentBlockedPhase.OUTPUT:
                    result = await agent._call_model(messages, tools=[])
                    self.assertEqual(
                        (result, model.received_messages),
                        (success, [messages, messages]),
                    )
                else:
                    with self.assertRaises(ContentBlockedError) as caught:
                        await agent._call_model(messages, tools=[])
                    self.assertEqual(
                        (caught.exception, model.received_messages),
                        (error, [messages]),
                    )

    async def test_partial_output_and_review_stop_replay(self) -> None:
        """Partial output and review stops prevent retries and fallback."""
        cases = (
            ContentBlockedError(
                "blocked",
                phase=ContentBlockedPhase.OUTPUT,
                partial_output=True,
            ),
            ContentBlockedError(
                "blocked",
                provider="openai",
                original_code="misalignment_policy_violation",
                requires_review=True,
            ),
            ContentBlockedError(
                "blocked",
                provider="openai",
                original_code="BIO_POLICY",
                requires_review=True,
            ),
            ContentBlockedError(
                "blocked",
                phase=ContentBlockedPhase.OUTPUT,
                provider="custom",
                requires_review=True,
            ),
        )
        for error in cases:
            with self.subTest(error=vars(error)):
                model = MockModel()
                model.set_responses([error, _response("unexpected")])
                fallback = MockModel()
                fallback.set_responses([_response("unexpected fallback")])
                agent = self._agent(model, retries=3)
                agent.model_config.fallback_model = fallback
                with self.assertRaises(ContentBlockedError) as caught:
                    await agent._call_model(messages=[], tools=[])
                self.assertEqual(
                    (caught.exception, model.cnt, fallback.cnt),
                    (error, 1, 0),
                )

    async def test_output_retry_budget_is_bounded(self) -> None:
        """Repeated OUTPUT blocks stop at the existing agent retry limit."""
        model = MockModel()
        errors = [
            ContentBlockedError("blocked", phase=ContentBlockedPhase.OUTPUT)
            for _ in range(3)
        ]
        model.set_responses(errors)
        agent = self._agent(model, retries=2)
        with self.assertRaises(ContentBlockedError) as caught:
            await agent._call_model(messages=[], tools=[])
        self.assertEqual((caught.exception, model.cnt), (errors[-1], 3))

    async def test_agent_preserves_normalized_error_cause(self) -> None:
        """The agent retains the SDK error attached by normalization."""
        original = RuntimeError("provider error")
        model = _NormalizingModel()
        model.set_responses([original])
        agent = self._agent(model)
        with self.assertRaises(ContentBlockedError) as caught:
            await agent._call_model(messages=[], tools=[])
        self.assertEqual(
            (caught.exception.__cause__, model.cnt),
            (original, 1),
        )

    async def test_ordinary_error_keeps_existing_retry(self) -> None:
        """Ordinary failures retain the existing same-payload retry."""
        model = MockModel()
        success = _response("retried")
        model.set_responses([RuntimeError("temporary failure"), success])
        result = await self._agent(model, retries=1)._call_model(
            messages=[],
            tools=[],
        )
        self.assertEqual((result, model.cnt), (success, 2))

    async def test_stream_errors_do_not_retry_or_switch_models(self) -> None:
        """All stream phases retain accurate partial-output state."""
        for phase in ContentBlockedPhase:
            for partial in (False, True):
                with self.subTest(phase=phase, partial=partial):
                    error = ContentBlockedError("blocked", phase=phase)
                    chunks = (
                        [_response("partial", is_last=False)]
                        if partial
                        else []
                    )
                    model = MockModel()
                    model.set_responses([[*chunks, error]])
                    fallback = MockModel()
                    fallback.set_responses([_response("unexpected")])
                    agent = self._agent(model, retries=3)
                    agent.model_config.fallback_model = fallback
                    stream = await agent._call_model(messages=[], tools=[])
                    seen = []
                    with self.assertLogs("as", level="WARNING") as logs:
                        with self.assertRaises(ContentBlockedError) as caught:
                            async for chunk in stream:
                                seen.append(chunk)
                    self.assertEqual(
                        (
                            seen,
                            caught.exception,
                            error.partial_output,
                            model.cnt,
                            fallback.cnt,
                        ),
                        (chunks, error, partial, 1, 0),
                    )
                    self.assertIn(
                        "during stream consumption",
                        "\n".join(logs.output),
                    )

    async def test_closing_observer_closes_delegated_stream(self) -> None:
        """Early consumer closure deterministically closes the inner stream."""
        closed = []
        expected = _response("first", is_last=False)

        async def stream() -> Any:
            """Record cleanup of a partially consumed provider stream."""
            try:
                yield expected
                yield _response("second", is_last=False)
            finally:
                closed.append(True)

        model = MockModel()
        observed = self._agent(model)._observe_model_stream(stream(), model)
        first = await anext(observed)
        self.assertEqual((first, closed), (expected, []))
        await observed.aclose()
        self.assertEqual(closed, [True])
