# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAIResponseModel response parsing.

Formatter tests have been moved to tests/formatter_openai_response_test.py.
"""
import json
from typing import Any
from datetime import datetime
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import OpenAIResponseModel
from agentscope.tool import ToolChoice
from agentscope.credential import OpenAICredential


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model() -> Any:
    return OpenAIResponseModel(
        credential=OpenAICredential(api_key="test"),
        model="o4-mini",
        stream=False,
        context_size=200_000,
    )


# ---------------------------------------------------------------------------
# Model response parsing tests
# ---------------------------------------------------------------------------


class TestOpenAIResponseModelParsing(IsolatedAsyncioTestCase):
    """Unit tests for OpenAIResponseModel response parsing."""

    def setUp(self) -> None:
        """Set up a fresh model instance and start time."""
        self.model = _make_model()
        self.start = datetime.now()

    def _mock_response(
        self,
        text: Any = None,
        function_calls: Any = None,
        reasoning_summary: Any = None,
        reasoning_id: str = "rs_test123",
    ) -> "MagicMock":
        """Build a mock OpenAI Responses API response object."""
        output = []

        if reasoning_summary:
            reasoning_item = MagicMock()
            reasoning_item.type = "reasoning"
            reasoning_item.id = reasoning_id
            summary_mock = MagicMock()
            summary_mock.text = reasoning_summary
            reasoning_item.summary = [summary_mock]
            output.append(reasoning_item)

        if text:
            msg_item = MagicMock()
            msg_item.type = "message"
            part = MagicMock()
            part.type = "output_text"
            part.text = text
            msg_item.content = [part]
            output.append(msg_item)

        if function_calls:
            for fc in function_calls:
                fc_item = MagicMock()
                fc_item.type = "function_call"
                fc_item.id = fc["id"]
                fc_item.call_id = fc["call_id"]
                fc_item.name = fc["name"]
                fc_item.arguments = fc["arguments"]
                output.append(fc_item)

        resp = MagicMock()
        resp.id = "resp-openai-1"
        resp.output = output
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        return resp

    def test_parse_text_response(self) -> None:
        """Parsing a text response creates a TextBlock."""
        resp = self._mock_response(text="Hello!")
        result = self.model._parse_completion_response(self.start, resp)
        self.assertTrue(result.is_last)
        texts = [b for b in result.content if isinstance(b, TextBlock)]
        self.assertEqual(texts[0].text, "Hello!")

    def test_parse_tool_call_response(self) -> None:
        """Parsing a function_call response creates a ToolCallBlock."""
        resp = self._mock_response(
            function_calls=[
                {
                    "id": "fc_abc123",
                    "call_id": "call-1",
                    "name": "get_weather",
                    "arguments": '{"city":"Beijing"}',
                },
            ],
        )
        result = self.model._parse_completion_response(self.start, resp)
        tcs = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].id, "fc_abc123")
        self.assertEqual(tcs[0].call_id, "call-1")
        self.assertEqual(tcs[0].name, "get_weather")
        self.assertEqual(json.loads(tcs[0].input)["city"], "Beijing")

    def test_parse_reasoning_response(self) -> None:
        """Parsing a reasoning item creates a ThinkingBlock with item id."""
        resp = self._mock_response(
            reasoning_summary="Thinking step...",
            text="Answer",
            reasoning_id="rs_abc999",
        )
        result = self.model._parse_completion_response(self.start, resp)
        thinkings = [b for b in result.content if isinstance(b, ThinkingBlock)]
        self.assertEqual(len(thinkings), 1)
        self.assertEqual(thinkings[0].thinking, "Thinking step...")
        self.assertEqual(
            getattr(thinkings[0], "reasoning_item_id", None),
            "rs_abc999",
        )

    def test_response_id_set(self) -> None:
        """The response ID from the API is stored in the ChatResponse."""
        resp = self._mock_response(text="Hi")
        result = self.model._parse_completion_response(self.start, resp)
        self.assertEqual(result.id, "resp-openai-1")


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


_FT_TOOLS_RESPONSE = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Get the weather",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "type": "function",
        "name": "get_time",
        "description": "Get the time",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
            "required": ["timezone"],
        },
    },
]


# pylint: disable=protected-access
class TestOpenAIResponseFormatTools(unittest.TestCase):
    """Tests for OpenAIResponseModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode returns flat tools and string 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """None mode returns flat tools and string 'none'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "none")

    def test_required_mode(self) -> None:
        """Required mode returns flat tools and string 'required'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="required"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "required")

    def test_str_mode_force_call(self) -> None:
        """A specific tool name returns a type=function dict with name."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(
            fmt_choice,
            {"type": "function", "name": "get_weather"},
        )

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["name"], "get_weather")
        self.assertEqual(fmt_choice, "auto")

    def test_no_tool_choice(self) -> None:
        """Without tool_choice, returns flat tools and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertIsNone(fmt_choice)
