# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for GeminiChatModel response parsing.

Formatter tests have been moved to tests/formatter_gemini_test.py.
"""
import json
from typing import Any
from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import GeminiChatModel, GeminiCredential


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model() -> Any:
    return GeminiChatModel(
        credential=GeminiCredential(api_key="test"),
        model="gemini-2.5-flash",
        stream=False,
        context_size=1_048_576,
        output_size=65_536,
    )


# ---------------------------------------------------------------------------
# Model response parsing tests
# ---------------------------------------------------------------------------


class TestGeminiModelParsing(IsolatedAsyncioTestCase):
    """Unit tests for GeminiChatModel response parsing."""

    def setUp(self) -> None:
        """Set up a fresh model instance and start time."""
        self.model = _make_model()
        self.start = datetime.now()

    def _mock_response(
        self,
        text: Any = None,
        function_calls: Any = None,
        thought: Any = None,
    ) -> "MagicMock":
        """Build a mock Gemini API response object."""
        parts = []
        if thought:
            p = MagicMock()
            p.text = thought
            p.thought = True
            p.function_call = None
            p.thought_signature = None
            parts.append(p)
        if text:
            p = MagicMock()
            p.text = text
            p.thought = False
            p.function_call = None
            p.thought_signature = None
            parts.append(p)
        if function_calls:
            for fc in function_calls:
                p = MagicMock()
                p.text = None
                p.thought = False
                p.thought_signature = None
                p.function_call.name = fc["name"]
                p.function_call.args = fc["args"]
                p.function_call.id = fc.get("id", "call-1")
                parts.append(p)

        resp = MagicMock()
        resp.candidates = [MagicMock()]
        resp.candidates[0].content.parts = parts
        resp.usage_metadata = MagicMock()
        resp.usage_metadata.prompt_token_count = 10
        resp.usage_metadata.candidates_token_count = 5
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
                    "name": "get_weather",
                    "args": {"city": "Beijing"},
                    "id": "call-1",
                },
            ],
        )
        result = self.model._parse_completion_response(self.start, resp)
        tcs = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].name, "get_weather")
        self.assertEqual(json.loads(tcs[0].input)["city"], "Beijing")

    def test_parse_thinking_response(self) -> None:
        """Parsing a response with thought creates a ThinkingBlock."""
        resp = self._mock_response(thought="Let me think...", text="Answer")
        result = self.model._parse_completion_response(self.start, resp)
        thinkings = [b for b in result.content if isinstance(b, ThinkingBlock)]
        self.assertEqual(len(thinkings), 1)
        self.assertEqual(thinkings[0].thinking, "Let me think...")
