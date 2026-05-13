# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OllamaChatModel response parsing.

Formatter tests have been moved to tests/formatter_ollama_test.py.
"""
import json
from typing import Any
from datetime import datetime
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import OllamaChatModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model() -> Any:
    return OllamaChatModel(
        model="qwen3:8b",
        stream=False,
        context_size=40_960,
    )


# ---------------------------------------------------------------------------
# Model response parsing tests
# ---------------------------------------------------------------------------


class TestOllamaModelParsing(IsolatedAsyncioTestCase):
    """Unit tests for OllamaChatModel response parsing."""

    def setUp(self) -> None:
        """Set up a fresh model instance and start time."""
        self.model = _make_model()
        self.start = datetime.now()

    def _mock_response(
        self,
        content: Any = None,
        tool_calls: Any = None,
        thinking: Any = None,
    ) -> "MagicMock":
        """Build a mock Ollama API response object."""
        msg = MagicMock()
        msg.content = content or ""
        setattr(msg, "thinking", thinking)

        if tool_calls:
            tc_mocks = []
            for tc in tool_calls:
                m = MagicMock()
                m.function.name = tc["name"]
                m.function.arguments = tc["args"]
                tc_mocks.append(m)
            msg.tool_calls = tc_mocks
        else:
            msg.tool_calls = None

        resp = MagicMock()
        resp.message = msg
        resp.prompt_eval_count = 10
        resp.eval_count = 5
        setattr(resp, "id", "ollama-1")
        return resp

    async def test_parse_text_response(self) -> None:
        """Parsing a text response creates a TextBlock."""
        resp = self._mock_response(content="Hello!")
        result = await self.model._parse_completion_response(self.start, resp)
        self.assertTrue(result.is_last)
        texts = [b for b in result.content if isinstance(b, TextBlock)]
        self.assertEqual(texts[0].text, "Hello!")

    async def test_parse_tool_call_response(self) -> None:
        """Parsing a tool-call response creates a ToolCallBlock."""
        resp = self._mock_response(
            tool_calls=[
                {"name": "get_weather", "args": {"city": "Beijing"}},
            ],
        )
        result = await self.model._parse_completion_response(self.start, resp)
        tcs = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].name, "get_weather")
        self.assertIn("city", json.loads(tcs[0].input))

    async def test_parse_thinking_response(self) -> None:
        """Parsing a response with thinking creates a ThinkingBlock."""
        resp = self._mock_response(
            content="Answer",
            thinking="Let me think...",
        )
        result = await self.model._parse_completion_response(self.start, resp)
        thinkings = [b for b in result.content if isinstance(b, ThinkingBlock)]
        self.assertEqual(thinkings[0].thinking, "Let me think...")


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


class TestOllamaFormatTools(unittest.TestCase):
    """Tests for OllamaChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_tools_forwarded_no_choice(self) -> None:
        """Without tool_choice, returns tools unchanged and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertIsNone(fmt_choice)

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            {"mode": "auto", "tools": ["get_weather"]},
        )
        self.assertIsNotNone(fmt_tools)
        assert fmt_tools is not None
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["function"]["name"], "get_weather")
        self.assertIsNone(fmt_choice)
