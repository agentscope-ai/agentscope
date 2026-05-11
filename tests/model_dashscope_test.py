# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for DashScopeChatModel response parsing.

Formatter tests have been moved to tests/formatter_dashscope_test.py.
"""
import json
from typing import Any
from datetime import datetime
from http import HTTPStatus
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import DashScopeChatModel, DashScopeCredential


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model() -> Any:
    return DashScopeChatModel(
        credential=DashScopeCredential(api_key="test"),
        model="qwen3-max",
        stream=False,
        context_size=1_000_000,
        output_size=65_536,
    )


# ---------------------------------------------------------------------------
# Model response parsing tests
# ---------------------------------------------------------------------------


class TestDashScopeModelParsing(IsolatedAsyncioTestCase):
    """Unit tests for DashScopeChatModel response parsing."""

    def setUp(self) -> None:
        """Set up a fresh model instance and start time."""
        self.model = _make_model()
        self.start = datetime.now()

    def _mock_response(
        self,
        content: Any = None,
        tool_calls: Any = None,
    ) -> "MagicMock":
        """Build a minimal DashScope GenerationResponse mock."""
        message = {}
        if content is not None:
            message["content"] = content
        if tool_calls is not None:
            message["tool_calls"] = tool_calls

        msg_mock = MagicMock()
        msg_mock.get = lambda key, default=None: message.get(key, default)

        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        resp.output.choices[0].message = msg_mock
        resp.request_id = "req-1"
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        return resp

    async def test_parse_text_response(self) -> None:
        """Parsing a text response creates a TextBlock."""
        resp = self._mock_response(content="Hello!")
        result = await self.model._parse_dashscope_generation_response(
            self.start,
            resp,
        )
        self.assertTrue(result.is_last)
        texts = [b for b in result.content if isinstance(b, TextBlock)]
        self.assertEqual(texts[0].text, "Hello!")

    async def test_parse_tool_call_response(self) -> None:
        """Parsing a tool-call response creates a ToolCallBlock."""
        tool_calls = [
            {
                "id": "call-1",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city":"Beijing"}',
                },
            },
        ]
        resp = self._mock_response(tool_calls=tool_calls)
        result = await self.model._parse_dashscope_generation_response(
            self.start,
            resp,
        )
        tcs = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].id, "call-1")
        self.assertEqual(tcs[0].name, "get_weather")
        self.assertEqual(json.loads(tcs[0].input)["city"], "Beijing")

    async def test_parse_response_with_status_error(self) -> None:
        """Non-OK status raises RuntimeError."""
        resp = self._mock_response(content="text")
        resp.status_code = HTTPStatus.BAD_REQUEST
        with self.assertRaises(RuntimeError):
            await self.model._parse_dashscope_generation_response(
                self.start,
                resp,
            )
