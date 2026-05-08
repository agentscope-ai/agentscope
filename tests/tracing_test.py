# -*- coding: utf-8 -*-
"""Unit tests for the tracing module using an in-memory OTel exporter."""
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from utils import MockModel

from agentscope.agent import Agent
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.model._model_usage import ChatUsage
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from agentscope.tool import Toolkit, ToolBase


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------


class WeatherTool(ToolBase):
    """Stub weather tool for tracing tests."""

    name: str = "get_weather"
    description: str = "Return stub weather for a city."
    input_schema: dict = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="always allowed",
        )

    async def execute(self, city: str) -> str:
        """Stub weather tool for tracing tests."""
        return f"{city}: sunny, 25°C."


def _make_tool_call_response(tool_id: str, city: str) -> ChatResponse:
    return ChatResponse(
        content=[
            ToolCallBlock(
                id=tool_id,
                name="get_weather",
                input=json.dumps({"city": city}),
            ),
        ],
        is_last=True,
        usage=ChatUsage(input_tokens=10, output_tokens=5, time=0.05),
    )


def _make_text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[TextBlock(text=text)],
        is_last=True,
        usage=ChatUsage(input_tokens=15, output_tokens=8, time=0.05),
    )


class TracingTest(IsolatedAsyncioTestCase):
    """Tests that OTel spans are emitted with correct attributes.

    The in-memory exporter is set up once per class (setUpClass) because
    the OTel global TracerProvider cannot be replaced once installed.
    setUp only creates fresh model/agent and clears the exporter.
    """

    exporter: InMemorySpanExporter

    @classmethod
    def setUpClass(cls) -> None:
        """Configure an in-memory OTel provider once for the whole class."""
        cls.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(cls.exporter))
        otel_trace.set_tracer_provider(provider)

    def setUp(self) -> None:
        """Create a fresh agent and clear accumulated spans before each
        test."""
        self.exporter.clear()
        self.model = MockModel()
        self.agent = Agent(
            name="test-agent",
            system_prompt="You are a test assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[WeatherTool()]),
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _spans_by_name(self, fragment: str) -> list:
        return [
            s
            for s in self.exporter.get_finished_spans()
            if fragment in (s.name or "")
        ]

    def _all_conv_ids(self) -> set:
        return {
            dict(s.attributes or {}).get("gen_ai.conversation.id")
            for s in self.exporter.get_finished_spans()
            if "gen_ai.conversation.id" in (s.attributes or {})
        }

    # -----------------------------------------------------------------------
    # Tests: Agent.reply
    # -----------------------------------------------------------------------

    async def test_reply_creates_invoke_agent_span(self) -> None:
        """Agent.reply must produce an invoke_agent span."""
        self.model.set_responses(
            [
                _make_tool_call_response("c1", "Beijing"),
                _make_text_response("It is 25°C in Beijing."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Beijing?")
        result = await self.agent.reply(msg)

        self.assertIsNotNone(result)
        agent_spans = self._spans_by_name("invoke_agent")
        self.assertGreater(
            len(agent_spans),
            0,
            "Expected at least one invoke_agent span",
        )

    async def test_reply_creates_execute_tool_span(self) -> None:
        """Agent.reply with a tool call must produce an execute_tool span."""
        self.model.set_responses(
            [
                _make_tool_call_response("c2", "Shanghai"),
                _make_text_response("Shanghai is rainy."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Shanghai?")
        await self.agent.reply(msg)

        tool_spans = self._spans_by_name("execute_tool")
        self.assertGreater(
            len(tool_spans),
            0,
            "Expected at least one execute_tool span",
        )

    async def test_reply_spans_share_conversation_id(self) -> None:
        """All spans from a single reply must share the same
        conversation_id."""
        self.model.set_responses(
            [
                _make_tool_call_response("c3", "Guangzhou"),
                _make_text_response("Guangzhou is sunny."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Guangzhou?")
        await self.agent.reply(msg)

        conv_ids = self._all_conv_ids()
        self.assertEqual(
            len(conv_ids),
            1,
            f"All spans must share exactly one conversation_id, "
            f"got: {conv_ids}",
        )

    async def test_invoke_agent_span_has_response_attributes(self) -> None:
        """invoke_agent span must carry gen_ai.output.messages attribute."""
        self.model.set_responses(
            [
                _make_tool_call_response("c4", "Wuhan"),
                _make_text_response("Wuhan weather: clear sky."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Wuhan?")
        await self.agent.reply(msg)

        agent_spans = self._spans_by_name("invoke_agent")
        self.assertTrue(agent_spans)
        span_attrs = dict(agent_spans[0].attributes or {})
        self.assertIn(
            "gen_ai.output.messages",
            span_attrs,
            "invoke_agent span should have gen_ai.output.messages attribute",
        )

    async def test_invoke_agent_span_has_input_attributes(self) -> None:
        """invoke_agent span must carry gen_ai.input.messages attribute."""
        self.model.set_responses(
            [
                _make_text_response("Simple answer."),
            ],
        )
        msg = UserMsg(name="user", content="Simple question?")
        await self.agent.reply(msg)

        agent_spans = self._spans_by_name("invoke_agent")
        self.assertTrue(agent_spans)
        span_attrs = dict(agent_spans[0].attributes or {})
        self.assertIn(
            "gen_ai.input.messages",
            span_attrs,
            "invoke_agent span should have gen_ai.input.messages attribute",
        )

    # -----------------------------------------------------------------------
    # Tests: Agent.reply_stream
    # -----------------------------------------------------------------------

    async def test_reply_stream_creates_invoke_agent_span(self) -> None:
        """Agent.reply_stream must produce an invoke_agent span."""
        self.model.set_responses(
            [
                _make_tool_call_response("c5", "Shenzhen"),
                _make_text_response("Shenzhen is warm."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Shenzhen?")
        events = [e async for e in self.agent.reply_stream(msg)]

        self.assertGreater(len(events), 0, "Expected at least one event")
        agent_spans = self._spans_by_name("invoke_agent")
        self.assertGreater(
            len(agent_spans),
            0,
            "Expected at least one invoke_agent span from reply_stream",
        )

    async def test_reply_stream_creates_execute_tool_span(self) -> None:
        """Agent.reply_stream with tool call must produce an execute_tool
        span."""
        self.model.set_responses(
            [
                _make_tool_call_response("c6", "Chengdu"),
                _make_text_response("Chengdu is cloudy."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Chengdu?")
        async for _ in self.agent.reply_stream(msg):
            pass

        tool_spans = self._spans_by_name("execute_tool")
        self.assertGreater(
            len(tool_spans),
            0,
            "Expected execute_tool span from reply_stream",
        )

    async def test_reply_stream_spans_share_conversation_id(self) -> None:
        """All spans from reply_stream must share the same conversation_id."""
        self.model.set_responses(
            [
                _make_tool_call_response("c7", "Hangzhou"),
                _make_text_response("Hangzhou is foggy."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Hangzhou?")
        async for _ in self.agent.reply_stream(msg):
            pass

        conv_ids = self._all_conv_ids()
        self.assertEqual(
            len(conv_ids),
            1,
            f"All spans must share one conversation_id, got: {conv_ids}",
        )

    async def test_execute_tool_span_has_tool_name_attribute(self) -> None:
        """execute_tool span must have the correct gen_ai.tool.name
        attribute."""
        self.model.set_responses(
            [
                _make_tool_call_response("c8", "Nanjing"),
                _make_text_response("Nanjing result."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Nanjing?")
        await self.agent.reply(msg)

        tool_spans = self._spans_by_name("execute_tool")
        self.assertTrue(tool_spans)
        span_attrs = dict(tool_spans[0].attributes or {})
        self.assertEqual(
            span_attrs.get("gen_ai.tool.name"),
            "get_weather",
            "execute_tool span should have gen_ai.tool.name = get_weather",
        )
