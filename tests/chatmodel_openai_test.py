# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAIChatModel"""
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


def _make_model() -> Any:
    with patch("openai.AsyncClient"):
        from agentscope.model import OpenAIChatModel
        from agentscope.formatter import OpenAIChatFormatter

        return OpenAIChatModel(
            model_name="gpt-4o",
            api_key="test",
            context_length=128000,
            stream=False,
            formatter=OpenAIChatFormatter(),
        )


class TestOpenAIFormatTools(unittest.TestCase):
    """Tests for OpenAIChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up test model instance."""
        self.model = _make_model()

    # ------------------------------------------------------------------
    # Basic cases
    # ------------------------------------------------------------------

    def test_tools_no_choice(self) -> None:
        """Tools are forwarded unchanged when tool_choice is None."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            None,
        )
        self.assertEqual(fmt_tools, TOOLS)
        self.assertIsNone(fmt_choice)

    def test_auto_mode(self) -> None:
        """auto mode produces 'auto' tool_choice."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "auto"},
        )
        self.assertEqual(fmt_tools, TOOLS)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """none mode produces 'none' tool_choice."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "none"},
        )
        self.assertEqual(fmt_tools, TOOLS)
        self.assertEqual(fmt_choice, "none")

    def test_required_mode(self) -> None:
        """required mode produces 'required' tool_choice."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "required"},
        )
        self.assertEqual(fmt_tools, TOOLS)
        self.assertEqual(fmt_choice, "required")

    # ------------------------------------------------------------------
    # str mode (force specific tool)
    # ------------------------------------------------------------------

    def test_str_mode_force_call(self) -> None:
        """Named-function mode forces a specific tool call."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "get_weather"},
        )
        # Tools must not be filtered
        self.assertEqual(fmt_tools, TOOLS)
        self.assertEqual(
            fmt_choice,
            {"type": "function", "function": {"name": "get_weather"}},
        )

    def test_str_mode_with_tools_whitelist(self) -> None:
        """Named-function mode with tools whitelist still forwards all
        tools."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "get_weather", "tools": ["get_weather", "search"]},
        )
        self.assertEqual(fmt_tools, TOOLS)
        self.assertEqual(
            fmt_choice,
            {"type": "function", "function": {"name": "get_weather"}},
        )

    def test_tools_filtered_by_tool_choice_tools(self) -> None:
        """tool_choice.tools filters the schemas list to specified tools."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "auto", "tools": ["get_weather"]},
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["function"]["name"], "get_weather")
        self.assertEqual(fmt_choice, "auto")


if __name__ == "__main__":
    unittest.main()
