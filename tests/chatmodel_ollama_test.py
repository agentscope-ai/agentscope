# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OllamaChatModel"""
import unittest
from typing import Any


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
    """Create an OllamaChatModel instance for testing."""
    from agentscope.model import OllamaChatModel
    from agentscope.formatter import OllamaChatFormatter

    return OllamaChatModel(
        model_name="llama3",
        stream=False,
        formatter=OllamaChatFormatter(),
    )


class TestOllamaFormatTools(unittest.TestCase):
    """Tests for OllamaChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up test model instance."""
        self.model = _make_model()

    def test_tools_forwarded_no_choice(self) -> None:
        """Ollama doesn't support tool_choice; tools are always forwarded."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            None,
        )
        self.assertEqual(fmt_tools, TOOLS)
        self.assertIsNone(fmt_choice)

    def test_tool_choice_ignored(self) -> None:
        """Ollama ignores tool_choice and always returns None for it."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "auto"},
        )
        self.assertIsNone(fmt_choice)
        self.assertEqual(fmt_tools, TOOLS)

    def test_str_mode_ignored(self) -> None:
        """Ollama ignores tool_choice and always returns None for it."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "get_weather"},
        )
        self.assertIsNone(fmt_choice)
        self.assertEqual(fmt_tools, TOOLS)

    def test_tools_filtered_by_tool_choice_tools(self) -> None:
        """Ollama filters tools even though tool_choice is not forwarded."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "auto", "tools": ["get_weather"]},
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["function"]["name"], "get_weather")
        self.assertIsNone(fmt_choice)


if __name__ == "__main__":
    unittest.main()
