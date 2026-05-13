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
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ThinkingBlock,
)
from agentscope.model import XAIChatModel
from agentscope.credential import XAICredential
from agentscope.tool import ToolChoice


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


def _extend_xai_stub_for_tools() -> None:
    """Add tool and required_tool stubs to the existing xai_sdk stub."""
    xai_chat = sys.modules.get("xai_sdk.chat")
    if xai_chat is None or hasattr(xai_chat, "required_tool"):
        return

    class _RequiredTool:
        """Minimal stub for xai_sdk.chat.required_tool(name)."""

        def __init__(self, tool_name: str) -> None:
            self.tool_name = tool_name

        def __eq__(self, other: object) -> bool:
            """Compare by tool_name."""
            return (
                isinstance(other, _RequiredTool)
                and self.tool_name == other.tool_name
            )

    def _tool_stub(
        name: str,
        description: str = "",
        parameters: Any = None,
    ) -> MagicMock:
        """Minimal stub for xai_sdk.chat.tool(name, ...)."""
        m = MagicMock()
        m.name = name
        m.description = description
        m.parameters = parameters or {}
        return m

    xai_chat.required_tool = _RequiredTool
    xai_chat.tool = _tool_stub


_extend_xai_stub_for_tools()


class TestXAIFormatTools(unittest.TestCase):
    """Tests for XAIChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_no_tool_choice(self) -> None:
        """Without tool_choice, returns xai_sdk tools and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertIsNotNone(fmt_tools)
        self.assertEqual(len(fmt_tools), 2)
        self.assertIsNone(fmt_choice)

    def test_auto_mode(self) -> None:
        """Auto mode returns xai_sdk tools and string 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertIsNotNone(fmt_tools)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """None mode returns xai_sdk tools and string 'none'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertIsNotNone(fmt_tools)
        self.assertEqual(fmt_choice, "none")

    def test_str_mode_force_call(self) -> None:
        """A specific tool name returns a required_tool stub object."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertIsNotNone(fmt_tools)
        self.assertEqual(fmt_choice.tool_name, "get_weather")

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0].name, "get_weather")
        self.assertEqual(fmt_choice, "auto")
