# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for DashScopeChatModel response parsing.

Formatter tests have been moved to tests/formatter_dashscope_test.py.
"""
import json
from typing import Any
from datetime import datetime
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import DashScopeChatModel
from agentscope.credential import DashScopeCredential
from agentscope.tool import ToolChoice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model() -> Any:
    return DashScopeChatModel(
        credential=DashScopeCredential(api_key="test"),
        model="qwen3-max",
        stream=False,
        max_retries=3,
        context_size=int(1500),
        parameters=DashScopeChatModel.Parameters(
            max_tokens=1000,
            thinking_enable=True,
            thinking_budget=100,
        ),
    )


# ---------------------------------------------------------------------------
# Model response parsing tests
# ---------------------------------------------------------------------------


class TestDashScopeModelParsing(IsolatedAsyncioTestCase):
    """Unit tests for DashScopeChatModel response parsing."""

    def setUp(self) -> None:
        """Set up a fresh model instance and start time."""
        self.model = _make_model()
        self.start = datetime.now()

    def _mock_response(
        self,
        content: Any = None,
        tool_calls: Any = None,
        reasoning_content: Any = None,
    ) -> "MagicMock":
        """Build a minimal OpenAI ChatCompletion-style mock."""
        message = MagicMock()
        message.content = content
        message.tool_calls = tool_calls
        message.reasoning_content = reasoning_content

        choice = MagicMock()
        choice.message = message

        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.prompt_tokens_details = None

        resp = MagicMock()
        resp.id = "req-1"
        resp.choices = [choice]
        resp.usage = usage
        return resp

    async def test_parse_text_response(self) -> None:
        """Parsing a text response creates a TextBlock."""
        resp = self._mock_response(content="Hello!")
        result = self.model._parse_completion_response(
            self.start,
            resp,
        )
        self.assertTrue(result.is_last)
        texts = [b for b in result.content if isinstance(b, TextBlock)]
        self.assertEqual(texts[0].text, "Hello!")

    async def test_parse_tool_call_response(self) -> None:
        """Parsing a tool-call response creates a ToolCallBlock."""
        tc_mock = MagicMock()
        tc_mock.id = "call-1"
        tc_mock.function.name = "get_weather"
        tc_mock.function.arguments = '{"city":"Beijing"}'

        resp = self._mock_response(tool_calls=[tc_mock])
        result = self.model._parse_completion_response(
            self.start,
            resp,
        )
        tcs = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].id, "call-1")
        self.assertEqual(tcs[0].name, "get_weather")
        self.assertEqual(json.loads(tcs[0].input)["city"], "Beijing")


# ---------------------------------------------------------------------------
# Shared _format_tools fixtures
# ---------------------------------------------------------------------------

_FT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the time",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string"}},
                "required": ["timezone"],
            },
        },
    },
]


class TestDashScopeFormatTools(unittest.TestCase):
    """Tests for DashScopeChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode returns tools unchanged and string 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """None mode returns tools unchanged and string 'none'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "none")

    def test_required_mode_warns(self) -> None:
        """Required mode emits a DeprecationWarning and falls back to auto."""
        with self.assertWarns(DeprecationWarning):
            fmt_tools, fmt_choice = self.model._format_tools(
                _FT_TOOLS,
                ToolChoice(mode="required"),
            )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "auto")

    def test_str_mode_force_call(self) -> None:
        """A specific tool name forces that tool call."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(
            fmt_choice,
            {"type": "function", "function": {"name": "get_weather"}},
        )

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["function"]["name"], "get_weather")
        self.assertEqual(fmt_choice, "auto")

    def test_no_tool_choice(self) -> None:
        """Without tool_choice, returns tools and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertIsNone(fmt_choice)
