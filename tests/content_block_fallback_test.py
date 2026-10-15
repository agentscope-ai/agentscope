# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for normalized content blocks and model-call fallback."""
from copy import deepcopy
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig, ModelConfig
from agentscope.exception import ContentBlockedError, ContentBlockedPhase
from agentscope.message import Msg, TextBlock, ToolCallBlock, UserMsg
from agentscope.middleware import (
    ContentBlockFallbackContext,
    ContentBlockFallbackDecision,
    ContentBlockFallbackMiddleware,
    ContentBlockFallbackStrategy,
)
from agentscope.model import ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit


class _MaskLatestStrategy(ContentBlockFallbackStrategy):
    """Replace the latest text message for each fallback attempt."""

    def __init__(self) -> None:
        """Initialize the recorded fallback contexts."""
        self.contexts: list[ContentBlockFallbackContext] = []

    async def recover(
        self,
        context: ContentBlockFallbackContext,
    ) -> ContentBlockFallbackDecision:
        """Return a deep-copied message list with the latest text masked."""
        self.contexts.append(context)
        messages = deepcopy(context.messages)
        for message in reversed(messages):
            for block in reversed(message.content):
                if isinstance(block, TextBlock):
                    block.text = f"<masked attempt='{context.attempt}'/>"
                    return ContentBlockFallbackDecision(messages=messages)
        return ContentBlockFallbackDecision(messages=messages)


class _InPlaceMaskStrategy(ContentBlockFallbackStrategy):
    """Mutate the defensive message copy supplied by the middleware."""

    async def recover(
        self,
        context: ContentBlockFallbackContext,
    ) -> ContentBlockFallbackDecision:
        """Mask the latest text block without making another copy."""
        for message in reversed(context.messages):
            for block in reversed(message.content):
                if isinstance(block, TextBlock):
                    block.text = "<masked/>"
                    return ContentBlockFallbackDecision(
                        messages=context.messages,
                    )
        return ContentBlockFallbackDecision(messages=context.messages)


class _TrackingModel(MockModel):
    """Record each message list received by the mock model."""

    def __init__(self) -> None:
        """Initialize the tracking model."""
        super().__init__()
        self.received_messages: list[list[Msg]] = []

    async def _call_api(self, *args: Any, **kwargs: Any) -> Any:
        """Record the messages before delegating to ``MockModel``."""
        self.received_messages.append(deepcopy(kwargs["messages"]))
        return await super()._call_api(*args, **kwargs)


class _ClosableStreamModel(MockModel):
    """Expose whether the delegated provider stream was closed."""

    def __init__(self) -> None:
        """Initialize the stream cleanup marker."""
        super().__init__(stream=True)
        self.closed = False

    async def _call_api(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Return a stream that records deterministic cleanup."""
        del args, kwargs

        async def _stream() -> Any:
            try:
                yield _response("first", is_last=False)
                yield _response("second", is_last=False)
            finally:
                self.closed = True

        return _stream()


def _blocked_error(*, partial_output: bool = False) -> ContentBlockedError:
    """Build a deterministic normalized content-block error."""
    return ContentBlockedError(
        "blocked",
        phase=ContentBlockedPhase.INPUT,
        provider="test",
        original_code="blocked_input",
        request_id="request-1",
        partial_output=partial_output,
    )


def _response(text: str, *, is_last: bool = True) -> ChatResponse:
    """Build a deterministic text response."""
    return ChatResponse(
        content=[TextBlock(id="text-1", text=text)],
        is_last=is_last,
    )


class ContentBlockFallbackTest(IsolatedAsyncioTestCase):
    """Test content-block normalization and fallback behavior."""

    def _agent(
        self,
        model: MockModel,
        strategy: ContentBlockFallbackStrategy | None = None,
        *,
        max_fallback_attempts: int = 3,
        model_retries: int = 0,
        toolkit: Toolkit | None = None,
    ) -> Agent:
        """Build an agent with optional content-block fallback."""
        middlewares = []
        if strategy is not None:
            middlewares.append(
                ContentBlockFallbackMiddleware(
                    strategy,
                    max_fallback_attempts=max_fallback_attempts,
                ),
            )
        return Agent(
            name="test-agent",
            system_prompt="test",
            model=model,
            toolkit=toolkit,
            middlewares=middlewares,
            model_config=ModelConfig(max_retries=model_retries),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    async def test_error_exposes_normalized_fields(self) -> None:
        """The error exposes the complete provider-independent structure."""
        error = _blocked_error(partial_output=True)

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
                "message": "blocked",
                "phase": ContentBlockedPhase.INPUT,
                "provider": "test",
                "original_code": "blocked_input",
                "request_id": "request-1",
                "partial_output": True,
            },
        )

    async def test_model_base_raises_normalized_error_with_cause(self) -> None:
        """The model hook normalizes SDK errors before retry handling."""

        class _NormalizingModel(MockModel):
            """Normalize every provider exception as a content block."""

            def _normalize_exception(self, error: Exception) -> Exception:
                """Translate the simulated provider error."""
                return _blocked_error()

        original = RuntimeError("provider error")
        model = _NormalizingModel()
        model.set_responses([original])

        with self.assertRaises(ContentBlockedError) as raised:
            await model(messages=[])

        self.assertIs(raised.exception.__cause__, original)
        self.assertEqual(model.cnt, 1)

    async def test_stream_raises_normalized_error_with_cause(self) -> None:
        """The model hook also normalizes errors raised during streaming."""

        class _NormalizingModel(MockModel):
            """Normalize every provider exception as a content block."""

            def _normalize_exception(self, error: Exception) -> Exception:
                """Translate the simulated provider error."""
                return _blocked_error()

        original = RuntimeError("stream provider error")
        model = _NormalizingModel()
        model.set_responses([[original]])
        result = await model(messages=[])

        with self.assertRaises(ContentBlockedError) as raised:
            async for _ in result:
                pass

        self.assertIs(raised.exception.__cause__, original)
        self.assertEqual(model.cnt, 1)

    async def test_non_stream_fallback_replaces_messages(self) -> None:
        """A strategy can replace messages and retry only the model call."""
        model = _TrackingModel()
        model.set_responses([_blocked_error(), _response("recovered")])
        strategy = _MaskLatestStrategy()
        agent = self._agent(model, strategy)

        result = await agent._call_model(
            messages=[UserMsg(name="user", content="unsafe")],
            tools=[],
        )

        self.assertIsInstance(result, ChatResponse)
        self.assertEqual(result.content[0].text, "recovered")
        self.assertEqual(model.cnt, 2)
        self.assertEqual(
            model.received_messages[1][-1].get_text_content(),
            "<masked attempt='1'/>",
        )
        self.assertListEqual(
            [context.attempt for context in strategy.contexts],
            [1],
        )

    async def test_strategy_cannot_mutate_original_messages(self) -> None:
        """In-place strategy edits affect only the defensive copy."""
        model = _TrackingModel()
        model.set_responses([_blocked_error(), _response("recovered")])
        original = UserMsg(name="user", content="unsafe")
        agent = self._agent(model, _InPlaceMaskStrategy())

        result = await agent._call_model(
            messages=[original],
            tools=[],
        )

        self.assertIsInstance(result, ChatResponse)
        self.assertEqual(original.get_text_content(), "unsafe")
        self.assertEqual(
            model.received_messages[1][-1].get_text_content(),
            "<masked/>",
        )

    async def test_fallback_limit_raises_last_error(self) -> None:
        """Recovery stops after the configured number of replacements."""
        model = _TrackingModel()
        model.set_responses([_blocked_error(), _blocked_error()])
        strategy = _MaskLatestStrategy()
        agent = self._agent(
            model,
            strategy,
            max_fallback_attempts=1,
        )

        with self.assertRaises(ContentBlockedError):
            await agent._call_model(
                messages=[UserMsg(name="user", content="unsafe")],
                tools=[],
            )

        self.assertEqual(model.cnt, 2)
        self.assertListEqual(
            [context.attempt for context in strategy.contexts],
            [1],
        )

    async def test_content_block_skips_same_payload_agent_retries(
        self,
    ) -> None:
        """Agent retries do not resend an unchanged blocked payload."""
        model = _TrackingModel()
        model.set_responses([_blocked_error()])
        agent = self._agent(model, model_retries=3)

        with self.assertRaises(ContentBlockedError):
            await agent._call_model(
                messages=[UserMsg(name="user", content="unsafe")],
                tools=[],
            )

        self.assertEqual(model.cnt, 1)

    async def test_agent_preserves_normalized_error_cause(self) -> None:
        """The agent keeps the provider exception attached as the cause."""

        class _NormalizingModel(_TrackingModel):
            """Normalize every provider exception as a content block."""

            def _normalize_exception(self, error: Exception) -> Exception:
                """Translate the simulated provider error."""
                return _blocked_error()

        original = RuntimeError("provider error")
        model = _NormalizingModel()
        model.set_responses([original])
        agent = self._agent(model)

        with self.assertRaises(ContentBlockedError) as raised:
            await agent._call_model(
                messages=[UserMsg(name="user", content="unsafe")],
                tools=[],
            )

        self.assertIs(raised.exception.__cause__, original)

    async def test_ordinary_error_keeps_existing_agent_retry(self) -> None:
        """Non-content failures retain the existing same-payload retry."""
        model = _TrackingModel()
        model.set_responses(
            [
                RuntimeError("temporary failure"),
                _response("retried"),
            ],
        )
        agent = self._agent(model, model_retries=1)

        result = await agent._call_model(
            messages=[UserMsg(name="user", content="safe")],
            tools=[],
        )

        self.assertIsInstance(result, ChatResponse)
        self.assertEqual(result.content[0].text, "retried")
        self.assertEqual(model.cnt, 2)

    async def test_stream_fallback_before_first_chunk(self) -> None:
        """A blocked stream can recover before emitting its first chunk."""
        model = _TrackingModel()
        model.set_responses(
            [
                [_blocked_error()],
                [_response("recovered", is_last=False)],
            ],
        )
        strategy = _MaskLatestStrategy()
        agent = self._agent(model, strategy)

        result = await agent._call_model(
            messages=[UserMsg(name="user", content="unsafe")],
            tools=[],
        )
        chunks = [chunk async for chunk in result]

        self.assertEqual(model.cnt, 2)
        self.assertListEqual(
            [chunk.content[0].text for chunk in chunks],
            ["recovered", "recovered"],
        )
        self.assertListEqual(
            [context.attempt for context in strategy.contexts],
            [1],
        )

    async def test_stream_does_not_replay_after_partial_output(self) -> None:
        """A block after an emitted chunk is surfaced without replay."""
        model = _TrackingModel()
        error = _blocked_error()
        model.set_responses(
            [
                [
                    _response("partial", is_last=False),
                    error,
                ],
            ],
        )
        strategy = _MaskLatestStrategy()
        agent = self._agent(model, strategy)

        result = await agent._call_model(
            messages=[UserMsg(name="user", content="unsafe")],
            tools=[],
        )
        chunks = []
        with self.assertRaises(ContentBlockedError) as raised:
            async for chunk in result:
                chunks.append(chunk)

        self.assertListEqual(
            [chunk.content[0].text for chunk in chunks],
            ["partial"],
        )
        self.assertTrue(raised.exception.partial_output)
        self.assertEqual(model.cnt, 1)
        self.assertListEqual(strategy.contexts, [])

    async def test_stream_content_block_is_logged(self) -> None:
        """A consumption-time content block emits an explicit warning."""
        model = _TrackingModel()
        model.set_responses([[_blocked_error()]])
        agent = self._agent(model)

        result = await agent._call_model(
            messages=[UserMsg(name="user", content="unsafe")],
            tools=[],
        )

        with self.assertLogs("as", level="WARNING") as captured:
            with self.assertRaises(ContentBlockedError):
                async for _ in result:
                    pass

        self.assertIn(
            "during stream consumption",
            "\n".join(captured.output),
        )

    async def test_closing_wrapped_stream_closes_provider_stream(self) -> None:
        """Closing the consumer stream deterministically reaches provider."""
        model = _ClosableStreamModel()
        agent = self._agent(model, _MaskLatestStrategy())
        result = await agent._call_model(
            messages=[UserMsg(name="user", content="safe")],
            tools=[],
        )

        first = await anext(result)
        self.assertEqual(first.content[0].text, "first")
        self.assertFalse(model.closed)

        await result.aclose()

        self.assertTrue(model.closed)

    async def test_fallback_does_not_repeat_completed_tool(self) -> None:
        """Retrying a blocked model call does not re-run the prior tool."""
        executions = 0

        async def side_effect(value: str) -> str:
            """Record one simulated side effect."""
            nonlocal executions
            executions += 1
            return f"result:{value}"

        tool = FunctionTool(
            side_effect,
            permission=PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="allowed",
            ),
        )
        model = _TrackingModel()
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call-1",
                            name="side_effect",
                            input='{"value": "once"}',
                        ),
                    ],
                    is_last=True,
                ),
                _blocked_error(),
                _response("done"),
            ],
        )
        strategy = _MaskLatestStrategy()
        agent = self._agent(
            model,
            strategy,
            toolkit=Toolkit(tools=[tool]),
        )

        reply = await agent.reply(UserMsg(name="user", content="run"))

        self.assertEqual(reply.get_text_content(), "done")
        self.assertEqual(executions, 1)
        self.assertEqual(model.cnt, 3)
