# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for GeminiChatModel"""
import unittest
from typing import Any, List, Tuple
from unittest.mock import MagicMock


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
    """Create a GeminiChatModel instance with stubbed google.genai SDK."""
    import sys
    import types

    # Stub out google.genai so no real SDK is needed
    google_mod = sys.modules.get("google") or types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    genai_types_mod = types.ModuleType("google.genai.types")
    genai_types_mod.GenerateContentResponse = MagicMock()

    mock_client = MagicMock()
    genai_mod.Client = MagicMock(return_value=mock_client)

    google_mod.genai = genai_mod
    sys.modules.setdefault("google", google_mod)
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = genai_types_mod

    from agentscope.model import GeminiChatModel
    from agentscope.formatter import GeminiChatFormatter

    return GeminiChatModel(
        model_name="gemini-1.5-pro",
        api_key="test",
        stream=False,
        formatter=GeminiChatFormatter(),
    )


class TestGeminiFormatTools(unittest.TestCase):
    """Tests for GeminiChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up test model instance."""
        self.model = _make_model()

    def _expected_fmt_tools(
        self,
        names: Tuple[str, ...] = ("get_weather", "search"),
    ) -> List[Any]:
        """Build expected formatted tools list for Gemini."""
        declarations = [
            {
                "name": n,
                "description": "Get weather"
                if n == "get_weather"
                else "Search",
                "parameters": {"type": "object", "properties": {}},
            }
            for n in names
        ]
        return [{"function_declarations": declarations}]

    def test_auto_mode(self) -> None:
        """auto mode produces AUTO function calling config."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "auto"},
        )
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "AUTO"}},
        )
        # All tools forwarded
        self.assertEqual(len(fmt_tools[0]["function_declarations"]), 2)

    def test_none_mode(self) -> None:
        """none mode produces NONE function calling config."""
        _, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "none"},
        )
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "NONE"}},
        )

    def test_required_mode(self) -> None:
        """required mode produces ANY function calling config."""
        _, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "required"},
        )
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "ANY"}},
        )

    def test_str_mode_force_call(self) -> None:
        """Named-function mode forces a specific allowed function name."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "get_weather"},
        )
        # Full tool list forwarded
        self.assertEqual(len(fmt_tools[0]["function_declarations"]), 2)
        self.assertEqual(
            fmt_choice,
            {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": ["get_weather"],
                },
            },
        )

    def test_tools_filtered_by_tool_choice_tools(self) -> None:
        """tool_choice.tools filters the schemas list to specified tools."""
        fmt_tools, fmt_choice = self.model._format_tools(
            TOOLS,
            {"mode": "auto", "tools": ["get_weather"]},
        )
        self.assertEqual(len(fmt_tools[0]["function_declarations"]), 1)
        self.assertEqual(
            fmt_tools[0]["function_declarations"][0]["name"],
            "get_weather",
        )
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "AUTO"}},
        )


if __name__ == "__main__":
    unittest.main()
