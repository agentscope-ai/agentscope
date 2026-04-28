# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for DashScopeChatModel"""
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
    """Create a DashScopeChatModel instance for testing."""
    with patch("openai.AsyncClient"):
        from agentscope.model import DashScopeChatModel
        from agentscope.formatter import DashScopeChatFormatter

        return DashScopeChatModel(
            model_name="qwen-max",
            api_key="test",
            stream=False,
            formatter=DashScopeChatFormatter(),
        )


class TestDashScopeFormatTools(unittest.TestCase):
    """Tests for DashScopeChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up test model instance."""
        self.model = _make_model()

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

    def test_required_mode_converts_to_auto(self) -> None:
        """DashScope doesn't support 'required'; it is converted to 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "required"},
        )
        self.assertEqual(fmt_tools, TOOLS)
        self.assertEqual(fmt_choice, "auto")

    def test_str_mode_force_call(self) -> None:
        """Named-function mode forces a specific tool call."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "get_weather"},
        )
        # Full tool list must be forwarded
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
