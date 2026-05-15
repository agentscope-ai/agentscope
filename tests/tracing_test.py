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
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import ChatResponse, ChatUsage
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from agentscope.tool import Toolkit, ToolBase
from agentscope.middleware import TracingMiddleware


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


class HitlWeatherTool(ToolBase):
    """Weather tool that always asks user for confirmation (HITL)."""

    name: str = "get_weather"
    description: str = "Return weather for a city, requires user confirmation."
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
            behavior=PermissionBehavior.ASK,
            message="user must confirm this tool call",
        )

    async def execute(self, city: str) -> str:
        """Stub weather tool for tracing tests."""
        return f"{city}: sunny, 25°C."


class ExternalWeatherTool(ToolBase):
    """Weather tool that is always executed externally."""

    name: str = "get_weather"
    description: str = "Return weather for a city via external execution."
    input_schema: dict = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = True  # Mark as external
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
            middlewares=[TracingMiddleware()],
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

    # -----------------------------------------------------------------------
    # Tests: reply_id attribute
    # -----------------------------------------------------------------------

    async def test_invoke_agent_span_has_reply_id(self) -> None:
        """invoke_agent span from a normal reply must carry reply_id.

        reply() delegates to _reply(), which is the only decorated entry
        point, so there is exactly one invoke_agent span.  We search by
        attribute rather than relying on list order for robustness.
        """
        self.model.set_responses(
            [
                _make_tool_call_response("r1", "Wuhan"),
                _make_text_response("Wuhan: clear sky."),
            ],
        )
        msg = UserMsg(name="user", content="Weather in Wuhan?")
        await self.agent.reply(msg)

        spans_with_reply_id = [
            s
            for s in self._spans_by_name("invoke_agent")
            if "agentscope.agent.reply_id" in (s.attributes or {})
        ]
        self.assertGreater(
            len(spans_with_reply_id),
            0,
            "At least one invoke_agent span should have "
            "agentscope.agent.reply_id",
        )

    # -----------------------------------------------------------------------
    # Tests: HITL (Human-in-the-loop)
    # -----------------------------------------------------------------------

    async def test_hitl_first_call_has_hitl_pending_attribute(self) -> None:
        """First call in HITL flow must have
        agentscope.agent.hitl_pending_tools."""
        hitl_agent = Agent(
            name="hitl-agent",
            system_prompt="You are a test assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[HitlWeatherTool()]),
            middlewares=[TracingMiddleware()],
        )
        # The HITL tool returns ASK;
        # first call ends with RequireUserConfirmEvent
        self.model.set_responses(
            [_make_tool_call_response("h1", "Beijing")],
        )
        self.exporter.clear()

        msg = UserMsg(name="user", content="Weather in Beijing?")
        async for _ in hitl_agent.reply_stream(msg):
            pass

        first_spans = self._spans_by_name("invoke_agent")
        self.assertTrue(
            first_spans,
            "Expected invoke_agent span from first HITL call",
        )
        span_attrs = dict(first_spans[0].attributes or {})
        self.assertIn(
            "agentscope.agent.hitl_pending_tools",
            span_attrs,
            "First HITL span should carry agentscope.agent.hitl_pending_tools",
        )
        pending = json.loads(span_attrs["agentscope.agent.hitl_pending_tools"])
        self.assertIn("get_weather", pending)

    async def test_hitl_spans_share_reply_id(self) -> None:
        """Both HITL calls must share the same agentscope.agent.reply_id."""
        hitl_agent = Agent(
            name="hitl-agent2",
            system_prompt="You are a test assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[HitlWeatherTool()]),
            middlewares=[TracingMiddleware()],
        )

        # First call: agent asks for confirmation
        self.model.set_responses(
            [_make_tool_call_response("h2", "Shanghai")],
        )
        self.exporter.clear()

        require_confirm_event = None
        async for evt in hitl_agent.reply_stream(
            UserMsg(name="user", content="Weather in Shanghai?"),
        ):
            if isinstance(evt, RequireUserConfirmEvent):
                require_confirm_event = evt

        self.assertIsNotNone(
            require_confirm_event,
            "Expected RequireUserConfirmEvent",
        )

        first_spans = self._spans_by_name("invoke_agent")
        first_reply_ids = {
            dict(s.attributes or {}).get("agentscope.agent.reply_id")
            for s in first_spans
            if "agentscope.agent.reply_id" in (s.attributes or {})
        }
        self.assertEqual(len(first_reply_ids), 1)
        reply_id_first = next(iter(first_reply_ids))

        # Second call: user confirms
        self.model.set_responses(
            [_make_text_response("Shanghai: 18°C, raining.")],
        )
        self.exporter.clear()

        confirm_event = UserConfirmResultEvent(
            reply_id=require_confirm_event.reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=require_confirm_event.tool_calls[0],
                ),
            ],
        )
        await hitl_agent.reply(event=confirm_event)

        second_spans = self._spans_by_name("invoke_agent")
        second_reply_ids = {
            dict(s.attributes or {}).get("agentscope.agent.reply_id")
            for s in second_spans
            if "agentscope.agent.reply_id" in (s.attributes or {})
        }
        self.assertEqual(len(second_reply_ids), 1)
        reply_id_second = next(iter(second_reply_ids))

        self.assertEqual(
            reply_id_first,
            reply_id_second,
            "Both HITL calls must share the same reply_id",
        )

    async def test_hitl_second_call_has_incoming_event_type(self) -> None:
        """Second call in HITL flow must carry
        incoming_event_type=user_confirm_result."""
        hitl_agent = Agent(
            name="hitl-agent3",
            system_prompt="You are a test assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[HitlWeatherTool()]),
            middlewares=[TracingMiddleware()],
        )

        # First call: agent asks for confirmation
        self.model.set_responses(
            [_make_tool_call_response("h3", "Tianjin")],
        )
        self.exporter.clear()

        require_confirm_event = None
        async for evt in hitl_agent.reply_stream(
            UserMsg(name="user", content="Weather in Tianjin?"),
        ):
            if isinstance(evt, RequireUserConfirmEvent):
                require_confirm_event = evt

        self.assertIsNotNone(
            require_confirm_event,
            "Expected RequireUserConfirmEvent",
        )

        # Second call: user confirms
        self.model.set_responses(
            [_make_text_response("Tianjin: windy, 12°C.")],
        )
        self.exporter.clear()

        confirm_event = UserConfirmResultEvent(
            reply_id=require_confirm_event.reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=require_confirm_event.tool_calls[0],
                ),
            ],
        )
        await hitl_agent.reply(event=confirm_event)

        agent_spans = self._spans_by_name("invoke_agent")
        self.assertTrue(
            agent_spans,
            "Expected invoke_agent span from second HITL call",
        )
        spans_with_event_type = [
            s
            for s in agent_spans
            if dict(s.attributes or {}).get(
                "agentscope.agent.incoming_event_type",
            )
            == "user_confirm_result"
        ]
        self.assertGreater(
            len(spans_with_event_type),
            0,
            "Second HITL invoke_agent span should have "
            "incoming_event_type=user_confirm_result",
        )

    # -----------------------------------------------------------------------
    # Tests: External execution
    # -----------------------------------------------------------------------

    async def test_external_execution_first_call_has_pending_attribute(
        self,
    ) -> None:
        """First call must have
        agentscope.agent.external_execution_pending_tools."""
        ext_agent = Agent(
            name="ext-agent",
            system_prompt="You are a test assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[ExternalWeatherTool()]),
            middlewares=[TracingMiddleware()],
        )
        self.model.set_responses(
            [_make_tool_call_response("e1", "Guangzhou")],
        )
        self.exporter.clear()

        async for _ in ext_agent.reply_stream(
            UserMsg(name="user", content="Weather in Guangzhou?"),
        ):
            pass

        first_spans = self._spans_by_name("invoke_agent")
        self.assertTrue(first_spans)
        span_attrs = dict(first_spans[0].attributes or {})
        self.assertIn(
            "agentscope.agent.external_execution_pending_tools",
            span_attrs,
            "First external-execution span should carry "
            "agentscope.agent.external_execution_pending_tools",
        )
        pending = json.loads(
            span_attrs["agentscope.agent.external_execution_pending_tools"],
        )
        self.assertIn("get_weather", pending)

    async def test_external_execution_spans_share_reply_id(self) -> None:
        """Both external-execution calls must share the same reply_id."""
        ext_agent = Agent(
            name="ext-agent-rid",
            system_prompt="You are a test assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[ExternalWeatherTool()]),
            middlewares=[TracingMiddleware()],
        )

        # First call: agent requires external execution
        self.model.set_responses(
            [_make_tool_call_response("ex-r1", "Nanjing")],
        )
        self.exporter.clear()

        require_ext_event = None
        async for evt in ext_agent.reply_stream(
            UserMsg(name="user", content="Weather in Nanjing?"),
        ):
            if isinstance(evt, RequireExternalExecutionEvent):
                require_ext_event = evt

        self.assertIsNotNone(
            require_ext_event,
            "Expected RequireExternalExecutionEvent",
        )

        first_spans = self._spans_by_name("invoke_agent")
        first_reply_ids = {
            dict(s.attributes or {}).get("agentscope.agent.reply_id")
            for s in first_spans
            if "agentscope.agent.reply_id" in (s.attributes or {})
        }
        self.assertEqual(len(first_reply_ids), 1)
        reply_id_first = next(iter(first_reply_ids))

        # Second call: inject external result
        self.model.set_responses(
            [_make_text_response("Nanjing: clear, 18°C.")],
        )
        self.exporter.clear()

        ext_result = ExternalExecutionResultEvent(
            reply_id=require_ext_event.reply_id,
            execution_results=[
                ToolResultBlock(
                    id=require_ext_event.tool_calls[0].id,
                    name="get_weather",
                    output="Nanjing: clear, 18°C.",
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )
        await ext_agent.reply(event=ext_result)

        second_spans = self._spans_by_name("invoke_agent")
        second_reply_ids = {
            dict(s.attributes or {}).get("agentscope.agent.reply_id")
            for s in second_spans
            if "agentscope.agent.reply_id" in (s.attributes or {})
        }
        self.assertEqual(len(second_reply_ids), 1)
        reply_id_second = next(iter(second_reply_ids))

        self.assertEqual(
            reply_id_first,
            reply_id_second,
            "Both external-execution calls must share the same reply_id",
        )

    async def test_external_execution_second_call_has_synthetic_tool_span(
        self,
    ) -> None:
        """Second call with ExternalExecutionResultEvent must produce
        execute_tool span."""
        ext_agent = Agent(
            name="ext-agent2",
            system_prompt="You are a test assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[ExternalWeatherTool()]),
            middlewares=[TracingMiddleware()],
        )

        # First call
        self.model.set_responses(
            [_make_tool_call_response("e2", "Shenzhen")],
        )
        self.exporter.clear()

        require_ext_event = None
        async for evt in ext_agent.reply_stream(
            UserMsg(name="user", content="Weather in Shenzhen?"),
        ):
            if isinstance(evt, RequireExternalExecutionEvent):
                require_ext_event = evt

        self.assertIsNotNone(require_ext_event)

        # Second call: inject external result
        self.model.set_responses(
            [_make_text_response("Shenzhen: warm, 28°C.")],
        )
        self.exporter.clear()

        ext_result = ExternalExecutionResultEvent(
            reply_id=require_ext_event.reply_id,
            execution_results=[
                ToolResultBlock(
                    id=require_ext_event.tool_calls[0].id,
                    name="get_weather",
                    output="Shenzhen: warm, 28°C.",
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )
        await ext_agent.reply(event=ext_result)

        tool_spans = self._spans_by_name("execute_tool")
        self.assertTrue(
            tool_spans,
            "Expected synthetic execute_tool span for external execution",
        )
        span_attrs = dict(tool_spans[0].attributes or {})
        self.assertTrue(
            span_attrs.get("agentscope.agent.is_external_execution"),
            "Synthetic execute_tool span should have "
            "is_external_execution=True",
        )
        self.assertEqual(span_attrs.get("gen_ai.tool.name"), "get_weather")

    async def test_external_execution_second_call_has_incoming_event_type(
        self,
    ) -> None:
        """Second call span must have
        incoming_event_type=external_execution_result."""
        ext_agent = Agent(
            name="ext-agent3",
            system_prompt="You are a test assistant.",
            model=self.model,
            toolkit=Toolkit(tools=[ExternalWeatherTool()]),
            middlewares=[TracingMiddleware()],
        )

        # First call
        self.model.set_responses(
            [_make_tool_call_response("e3", "Chengdu")],
        )
        self.exporter.clear()

        require_ext_event = None
        async for evt in ext_agent.reply_stream(
            UserMsg(name="user", content="Weather in Chengdu?"),
        ):
            if isinstance(evt, RequireExternalExecutionEvent):
                require_ext_event = evt

        # Second call
        self.model.set_responses([_make_text_response("Chengdu: cloudy.")])
        self.exporter.clear()

        ext_result = ExternalExecutionResultEvent(
            reply_id=require_ext_event.reply_id,
            execution_results=[
                ToolResultBlock(
                    id=require_ext_event.tool_calls[0].id,
                    name="get_weather",
                    output="Chengdu: cloudy, 15°C.",
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )
        await ext_agent.reply(event=ext_result)

        agent_spans = self._spans_by_name("invoke_agent")
        self.assertTrue(agent_spans)
        span_attrs = dict(agent_spans[0].attributes or {})
        self.assertEqual(
            span_attrs.get("agentscope.agent.incoming_event_type"),
            "external_execution_result",
        )
