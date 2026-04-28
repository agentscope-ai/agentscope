# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for AnthropicChatModel"""
import unittest
from typing import Any
from unittest.mock import patch


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

FMT_TOOLS = [
    {
        "name": "get_weather",
        "description": "Get weather",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search",
        "description": "Search",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _make_model() -> Any:
    """Create an AnthropicChatModel instance for testing."""
    with patch("anthropic.AsyncAnthropic"):
        from agentscope.model import AnthropicChatModel
        from agentscope.formatter import AnthropicChatFormatter

        return AnthropicChatModel(
            model_name="claude-3-5-sonnet-20241022",
            api_key="test",
            stream=False,
            formatter=AnthropicChatFormatter(),
        )


class TestAnthropicFormatTools(unittest.TestCase):
    """Tests for AnthropicChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up test model instance."""
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """auto mode produces auto tool_choice and reformatted tools."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "auto"},
        )
        self.assertEqual(fmt_tools, FMT_TOOLS)
        self.assertEqual(fmt_choice, {"type": "auto"})

    def test_none_mode(self) -> None:
        """none mode produces none tool_choice."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "none"},
        )
        self.assertEqual(fmt_tools, FMT_TOOLS)
        self.assertEqual(fmt_choice, {"type": "none"})

    def test_required_mode(self) -> None:
        """required mode maps to any tool_choice for Anthropic."""
        (
            fmt_tools,
            fmt_choice,
        ) = self.model._format_tools(
            TOOLS,
            {"mode": "required"},
        )
        self.assertEqual(fmt_tools, FMT_TOOLS)
        self.assertEqual(fmt_choice, {"type": "any"})

    def test_str_mode_force_call(self) -> None:
        """Named-function mode forces a specific tool call."""
        (
            fmt_tools,
            fmt_choice,
        ) = self.model._format_tools(
            TOOLS,
            {"mode": "get_weather"},
        )
        # Full tool list forwarded (no filtering)
        self.assertEqual(fmt_tools, FMT_TOOLS)
        self.assertEqual(fmt_choice, {"type": "tool", "name": "get_weather"})

    def test_tools_filtered_by_tool_choice_tools(self) -> None:
        """tool_choice.tools filters the schemas list to specified tools."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "auto", "tools": ["get_weather"]},
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["name"], "get_weather")
        self.assertEqual(fmt_choice, {"type": "auto"})


if __name__ == "__main__":
    unittest.main()
