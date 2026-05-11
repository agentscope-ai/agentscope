# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAIChatModel response parsing.

Formatter tests have been moved to tests/formatter_openai_chat_test.py.
"""
import json
from typing import Any
from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import OpenAIChatModel, OpenAIChatCredential


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model() -> Any:
    return OpenAIChatModel(
        credential=OpenAIChatCredential(api_key="test"),
        model="gpt-4o",
        stream=False,
        context_size=128_000,
        output_size=4_096,
    )


# ---------------------------------------------------------------------------
# Model response parsing tests
# ---------------------------------------------------------------------------


class TestOpenAIChatModelParsing(IsolatedAsyncioTestCase):
    """Unit tests for OpenAIChatModel response parsing."""

    def setUp(self) -> None:
        """Set up a fresh model instance and start time."""
        self.model = _make_model()
        self.start = datetime.now()

    def _mock_completion(
        self,
        text: Any = None,
        tool_calls: Any = None,
        reasoning: Any = None,
    ) -> "MagicMock":
        """Build a minimal ChatCompletion mock."""
        msg = MagicMock()
        msg.content = text
        msg.tool_calls = None
        setattr(msg, "reasoning_content", reasoning)
        setattr(msg, "reasoning", None)

        if tool_calls:
            tc_mocks = []
            for tc in tool_calls:
                m = MagicMock()
                m.id = tc["id"]
                m.function.name = tc["name"]
                m.function.arguments = tc["arguments"]
                tc_mocks.append(m)
            msg.tool_calls = tc_mocks

        choice = MagicMock()
        choice.message = msg
        choice.delta = msg

        resp = MagicMock()
        resp.id = "resp-1"
        resp.choices = [choice]
        resp.usage.prompt_tokens = 10
        resp.usage.completion_tokens = 5
        return resp

    def test_parse_text_response(self) -> None:
        """Parsing a text response creates a TextBlock."""
        resp = self._mock_completion(text="Hello!")
        result = self.model._parse_completion_response(self.start, resp)
        self.assertTrue(result.is_last)
        texts = [b for b in result.content if isinstance(b, TextBlock)]
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0].text, "Hello!")

    def test_parse_tool_call_response(self) -> None:
        """Parsing a tool-call response creates a ToolCallBlock."""
        resp = self._mock_completion(
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "get_weather",
                    "arguments": '{"city":"Beijing"}',
                },
            ],
        )
        result = self.model._parse_completion_response(self.start, resp)
        self.assertTrue(result.is_last)
        tcs = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].id, "call-1")
        self.assertEqual(tcs[0].name, "get_weather")
        self.assertEqual(json.loads(tcs[0].input)["city"], "Beijing")

    def test_parse_thinking_response(self) -> None:
        """Parsing a response with reasoning creates a ThinkingBlock."""
        resp = self._mock_completion(
            text="Answer",
            reasoning="Let me think...",
        )
        result = self.model._parse_completion_response(self.start, resp)
        thinkings = [b for b in result.content if isinstance(b, ThinkingBlock)]
        self.assertEqual(len(thinkings), 1)
        self.assertEqual(thinkings[0].thinking, "Let me think...")

    def test_response_id_set(self) -> None:
        """The response ID from the API is stored in the ChatResponse."""
        resp = self._mock_completion(text="Hi")
        result = self.model._parse_completion_response(self.start, resp)
        self.assertEqual(result.id, "resp-1")
