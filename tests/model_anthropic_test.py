# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for AnthropicChatModel response parsing.

Formatter tests have been moved to tests/formatter_anthropic_test.py.
"""
import json
from typing import Any
from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import AnthropicChatModel, AnthropicCredential


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model() -> Any:
    return AnthropicChatModel(
        credential=AnthropicCredential(api_key="test"),
        model="claude-opus-4-5",
        stream=False,
        context_size=200_000,
        output_size=8_192,
    )


# ---------------------------------------------------------------------------
# Model response parsing tests
# ---------------------------------------------------------------------------


class TestAnthropicModelParsing(IsolatedAsyncioTestCase):
    """Unit tests for AnthropicChatModel response parsing."""

    def setUp(self) -> None:
        """Set up a fresh model instance and start time."""
        self.model = _make_model()
        self.start = datetime.now()

    def _mock_anthropic_response(
        self,
        text: Any = None,
        tool_calls: Any = None,
        thinking: Any = None,
    ) -> "MagicMock":
        """Build a mock Anthropic API response object."""
        blocks = []
        if thinking:
            b = MagicMock()
            b.type = "thinking"
            b.thinking = thinking
            b.signature = ""
            blocks.append(b)
        if text:
            b = MagicMock()
            b.type = "text"
            b.text = text
            blocks.append(b)
        if tool_calls:
            for tc in tool_calls:
                b = MagicMock()
                b.type = "tool_use"
                b.id = tc["id"]
                b.name = tc["name"]
                b.input = tc["input"]
                blocks.append(b)

        resp = MagicMock()
        resp.id = "msg-1"
        resp.content = blocks
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        return resp

    async def test_parse_text_response(self) -> None:
        """Parsing a text response creates a TextBlock."""
        resp = self._mock_anthropic_response(text="Hello!")
        result = await self.model._parse_anthropic_completion_response(
            self.start,
            resp,
        )
        self.assertTrue(result.is_last)
        texts = [b for b in result.content if isinstance(b, TextBlock)]
        self.assertEqual(texts[0].text, "Hello!")

    async def test_parse_tool_call_response(self) -> None:
        """Parsing a tool-call response creates a ToolCallBlock."""
        resp = self._mock_anthropic_response(
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "get_weather",
                    "input": {"city": "Beijing"},
                },
            ],
        )
        result = await self.model._parse_anthropic_completion_response(
            self.start,
            resp,
        )
        tcs = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(tcs[0].id, "call-1")
        self.assertEqual(tcs[0].name, "get_weather")
        self.assertEqual(json.loads(tcs[0].input)["city"], "Beijing")

    async def test_parse_thinking_response(self) -> None:
        """Parsing a response with thinking creates a ThinkingBlock."""
        resp = self._mock_anthropic_response(
            thinking="Let me think...",
            text="Answer",
        )
        result = await self.model._parse_anthropic_completion_response(
            self.start,
            resp,
        )
        thinkings = [b for b in result.content if isinstance(b, ThinkingBlock)]
        self.assertEqual(thinkings[0].thinking, "Let me think...")

    async def test_response_id_set(self) -> None:
        """The response ID from the API is stored in the ChatResponse."""
        resp = self._mock_anthropic_response(text="Hi")
        result = await self.model._parse_anthropic_completion_response(
            self.start,
            resp,
        )
        self.assertEqual(result.id, "msg-1")
