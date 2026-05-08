# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for XAIChatModel (xAI) response parsing.

Formatter tests have been moved to tests/formatter_xai_test.py.
"""
import json
import sys
from typing import Any
from datetime import datetime
from types import ModuleType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ThinkingBlock,
)
from agentscope.model import XAIChatModel, XAICredential


# ---------------------------------------------------------------------------
# Build a lightweight xai_sdk stub so tests run without the real package.
# ---------------------------------------------------------------------------


def _build_xai_sdk_stub() -> None:
    """Register stub modules for xai_sdk so imports don't fail."""
    if "xai_sdk" in sys.modules:
        return

    # chat_pb2 stub -----------------------------------------------------------
    chat_pb2 = ModuleType("xai_sdk.chat.chat_pb2")

    class _EnumHelper:
        """Helper that makes .Value() return an integer."""

        _mapping = {
            "ROLE_ASSISTANT": 2,
            "TOOL_CALL_TYPE_CLIENT_SIDE_TOOL": 1,
        }

        def Value(self, name: str) -> int:
            """Return the integer value for the given enum name."""
            return self._mapping.get(name, 0)

    chat_pb2.MessageRole = _EnumHelper()
    chat_pb2.ToolCallType = _EnumHelper()

    class _RepeatedField(list):
        """Minimal repeated proto field that supports .add()."""

        def __init__(self, factory: Any) -> None:
            super().__init__()
            self._factory = factory

        def add(self) -> Any:
            """Add a new item using the factory and return it."""
            item = self._factory()
            self.append(item)
            return item

    class _FunctionSpec:
        name: str = ""
        arguments: str = ""

    class _ToolCallProto:
        id: str = ""
        type: int = 0
        function = _FunctionSpec()

    class _ContentPart:
        text: str = ""

    class _MessageProto:
        def __init__(self) -> None:
            self.role = 0
            self.content = _RepeatedField(_ContentPart)
            self.tool_calls = _RepeatedField(_ToolCallProto)

    chat_pb2.Message = _MessageProto

    # xai_sdk.chat stub -------------------------------------------------------
    xai_chat = ModuleType("xai_sdk.chat")
    xai_chat.chat_pb2 = chat_pb2
    xai_chat.user = lambda *args: MagicMock(role="user", args=args)
    xai_chat.assistant = lambda *args: MagicMock(role="assistant", args=args)
    xai_chat.system = lambda *args: MagicMock(role="system", args=args)
    xai_chat.tool_result = lambda *args, **kw: MagicMock(
        role="tool",
        args=args,
        kwargs=kw,
    )
    xai_chat.image = lambda url: MagicMock(type="image", url=url)

    # xai_sdk stub ------------------------------------------------------------
    xai_sdk = ModuleType("xai_sdk")
    xai_sdk.chat = xai_chat
    xai_sdk.AsyncClient = MagicMock()

    sys.modules["xai_sdk"] = xai_sdk
    sys.modules["xai_sdk.chat"] = xai_chat
    sys.modules["xai_sdk.chat.chat_pb2"] = chat_pb2


_build_xai_sdk_stub()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model() -> Any:
    return XAIChatModel(
        credential=XAICredential(api_key="test"),
        model="grok-3",
        stream=False,
        context_size=131_072,
        output_size=32_768,
    )


# ---------------------------------------------------------------------------
# Model response parsing tests
# ---------------------------------------------------------------------------


class TestXAIModelParsing(IsolatedAsyncioTestCase):
    """Unit tests for XAIChatModel response parsing."""

    def setUp(self) -> None:
        """Set up a fresh model instance and start time."""
        self.model = _make_model()
        self.start = datetime.now()

    def _mock_response(
        self,
        text: Any = None,
        tool_calls: Any = None,
        reasoning: Any = None,
    ) -> "MagicMock":
        """Build a mock xAI API response object."""
        resp = MagicMock()
        resp.id = "xai-resp-1"
        resp.content = text or ""
        resp.reasoning_content = reasoning or ""
        resp.tool_calls = None
        resp.usage = None

        if tool_calls:
            tc_mocks = []
            for tc in tool_calls:
                m = MagicMock()
                m.id = tc["id"]
                m.function.name = tc["name"]
                m.function.arguments = tc["arguments"]
                tc_mocks.append(m)
            resp.tool_calls = tc_mocks

        return resp

    def test_parse_text_response(self) -> None:
        """Parsing a text response creates a TextBlock."""
        resp = self._mock_response(text="Hello!")
        result = self.model._parse_completion_response(self.start, resp)
        self.assertTrue(result.is_last)
        texts = [b for b in result.content if isinstance(b, TextBlock)]
        self.assertEqual(texts[0].text, "Hello!")

    def test_parse_tool_call_response(self) -> None:
        """Parsing a tool call response creates a ToolCallBlock."""
        resp = self._mock_response(
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "get_weather",
                    "arguments": '{"city":"Beijing"}',
                },
            ],
        )
        result = self.model._parse_completion_response(self.start, resp)
        tcs = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(len(tcs), 1)
        self.assertEqual(tcs[0].id, "call-1")
        self.assertEqual(tcs[0].name, "get_weather")
        self.assertEqual(json.loads(tcs[0].input)["city"], "Beijing")

    def test_parse_thinking_response(self) -> None:
        """Parsing a response with reasoning creates a ThinkingBlock."""
        resp = self._mock_response(text="Answer", reasoning="Let me think...")
        result = self.model._parse_completion_response(self.start, resp)
        thinkings = [b for b in result.content if isinstance(b, ThinkingBlock)]
        self.assertEqual(len(thinkings), 1)
        self.assertEqual(thinkings[0].thinking, "Let me think...")

    def test_response_id_set(self) -> None:
        """The response ID from the API is stored in the ChatResponse."""
        resp = self._mock_response(text="Hi")
        result = self.model._parse_completion_response(self.start, resp)
        self.assertEqual(result.id, "xai-resp-1")
