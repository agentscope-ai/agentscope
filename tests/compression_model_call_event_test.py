# -*- coding: utf-8 -*-
"""Tests for the ModelCallEndEvent emitted by context compression."""
# pylint: disable=unused-argument
from typing import Awaitable, Callable
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, ContextConfig, InjectionConfig
from agentscope.event import ModelCallEndEvent
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    Usage,
    UserMsg,
)
from agentscope.middleware import MiddlewareBase
from agentscope.model import (
    ChatResponse,
    ChatUsage,
    FinishedReason,
    StructuredResponse,
)
from agentscope.state import AgentState
from agentscope.tool import Toolkit


_COMPRESSION_CONTENT = {
    "task_overview": "1",
    "current_state": "2",
    "important_discoveries": "3",
    "next_steps": "4",
    "context_to_preserve": "5",
}

_COMPRESSION_USAGE = ChatUsage(
    input_tokens=123,
    output_tokens=45,
    time=0.1,
    cache_input_tokens=6,
    cache_creation_input_tokens=7,
)

_REASONING_USAGE = ChatUsage(
    input_tokens=11,
    output_tokens=22,
    time=0.2,
    cache_input_tokens=3,
    cache_creation_input_tokens=4,
)


class ForwardingCompressMiddleware(MiddlewareBase):
    """Middleware that returns the downstream compression usage."""

    async def on_compress_context(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[ChatUsage | None]],
    ) -> ChatUsage | None:
        """Return the downstream value so the event survives."""
        return await next_handler(**input_kwargs)


class DroppingCompressMiddleware(MiddlewareBase):
    """Middleware that forwards but discards the downstream usage."""

    async def on_compress_context(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[ChatUsage | None]],
    ) -> ChatUsage | None:
        """Forward and intentionally drop the usage."""
        await next_handler(**input_kwargs)
        return None


def _make_agent(
    middlewares: list[MiddlewareBase] | None = None,
    compress: bool = True,
) -> tuple[Agent, MockModel]:
    """Build an agent whose reply triggers or skips compression."""
    model = MockModel(context_size=100)
    if compress:
        context = [
            UserMsg("User", "1" * 120, id="1"),
            AssistantMsg("Friday", "2" * 40, id="2"),
            UserMsg("User", "3" * 40, id="3"),
        ]
    else:
        context = [UserMsg("User", "hi", id="1")]
    agent = Agent(
        name="Friday",
        system_prompt="0" * 80,
        model=model,
        context_config=ContextConfig(
            trigger_ratio=0.7,
            reserve_ratio=0.4,
        ),
        injection_config=InjectionConfig(inject_runtime_state=False),
        state=AgentState(session_id="123", context=context),
        toolkit=Toolkit(),
        middlewares=middlewares or [],
    )
    model.set_structured_response(
        StructuredResponse(
            content=dict(_COMPRESSION_CONTENT),
            usage=_COMPRESSION_USAGE,
        ),
    )
    model.set_responses(
        [
            ChatResponse(
                content=[TextBlock(type="text", text="done")],
                is_last=True,
                usage=_REASONING_USAGE,
            ),
        ],
    )
    return agent, model


async def _collect_end_events(agent: Agent) -> list[ModelCallEndEvent]:
    """Drive one reply and return the emitted ModelCallEndEvent objects."""
    end_events: list[ModelCallEndEvent] = []
    async for event in agent.reply_stream(UserMsg("User", "continue")):
        if isinstance(event, ModelCallEndEvent):
            end_events.append(event)
    return end_events


class CompressionModelCallEventTest(IsolatedAsyncioTestCase):
    """Tests for issue #3930's compression ModelCallEndEvent."""

    async def test_compress_context_returns_observed_usage(self) -> None:
        """compress_context returns the usage recorded on state.context."""
        agent, _ = _make_agent()

        usage = await agent.compress_context()

        self.assertEqual(usage, _COMPRESSION_USAGE)
        self.assertEqual(
            agent.state.context[-1].usage,
            Usage(
                input_tokens=123,
                output_tokens=45,
                cache_input_tokens=6,
                cache_creation_input_tokens=7,
            ),
        )

    async def test_compress_context_returns_none_below_threshold(
        self,
    ) -> None:
        """No compression returns None, so no event is constructed."""
        agent, _ = _make_agent(compress=False)

        usage = await agent.compress_context()

        self.assertIsNone(usage)
        self.assertIsNone(agent.state.context[-1].usage)

    async def test_compression_emits_exactly_one_model_call_end_event(
        self,
    ) -> None:
        """The compression call emits one event, with usage matching state."""
        agent, _ = _make_agent()

        end_events = await _collect_end_events(agent)

        compression_events = [
            event for event in end_events if event.input_tokens == 123
        ]
        self.assertEqual(len(compression_events), 1)
        event = compression_events[0]
        self.assertEqual(event.reply_id, agent.state.reply_id)
        self.assertEqual(event.input_tokens, 123)
        self.assertEqual(event.output_tokens, 45)
        self.assertEqual(event.cache_input_tokens, 6)
        self.assertEqual(event.cache_creation_input_tokens, 7)
        self.assertEqual(
            event.finished_reason,
            FinishedReason.COMPLETED,
        )

        # The event's token counts agree with what compression recorded on
        # the retained context, so the event stream and state.context match.
        recorded = None
        for msg in agent.state.context:
            if msg.usage is not None and msg.usage.input_tokens == 123:
                recorded = msg.usage
                break
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.input_tokens, 123)
        self.assertEqual(recorded.output_tokens, 45)
        self.assertEqual(recorded.cache_input_tokens, 6)
        self.assertEqual(recorded.cache_creation_input_tokens, 7)

        # Ordinary reasoning still emits exactly its own single event.
        self.assertEqual(len(end_events), 2)
        reasoning_events = [
            event for event in end_events if event.input_tokens == 11
        ]
        self.assertEqual(len(reasoning_events), 1)

    async def test_no_compression_emits_no_extra_event(self) -> None:
        """A reply without compression keeps the single reasoning event."""
        agent, _ = _make_agent(compress=False)

        end_events = await _collect_end_events(agent)

        self.assertEqual(len(end_events), 1)
        self.assertEqual(end_events[0].input_tokens, 11)
        self.assertEqual(end_events[0].reply_id, agent.state.reply_id)

    async def test_compression_usage_survives_forwarding_middleware(
        self,
    ) -> None:
        """A middleware returning the downstream value keeps the event."""
        agent, _ = _make_agent(
            middlewares=[ForwardingCompressMiddleware()],
        )

        end_events = await _collect_end_events(agent)

        compression_events = [
            event for event in end_events if event.input_tokens == 123
        ]
        self.assertEqual(len(compression_events), 1)

    async def test_compression_usage_dropped_by_returning_none(self) -> None:
        """A middleware returning None intentionally drops the event."""
        agent, _ = _make_agent(
            middlewares=[DroppingCompressMiddleware()],
        )

        end_events = await _collect_end_events(agent)

        self.assertFalse(
            any(event.input_tokens == 123 for event in end_events),
        )
        # Compression itself still ran and recorded its usage on the context.
        self.assertTrue(
            any(
                msg.usage is not None and msg.usage.input_tokens == 123
                for msg in agent.state.context
            ),
        )
