# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for XAIChatFormatter and
XAIMultiAgentFormatter (xAI), following the reference test style.

Because these formatters return xai_sdk protobuf Message objects (not plain
dicts), a lightweight xai_sdk stub is built at module load so that tests run
without the real package.  Assertions check mock object attributes
(role, args, kwargs, tool_calls, etc.) rather than plain-dict equality.
"""
import sys
from typing import Any
from types import ModuleType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.formatter import XAIChatFormatter, XAIMultiAgentFormatter
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ThinkingBlock,
    ToolResultState,
)

# ---------------------------------------------------------------------------
# Build a lightweight xai_sdk stub so tests run without the real package.
# ---------------------------------------------------------------------------


def _build_xai_sdk_stub() -> None:
    if "xai_sdk" in sys.modules:
        return

    chat_pb2 = ModuleType("xai_sdk.chat.chat_pb2")

    class _EnumHelper:
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

    xai_sdk = ModuleType("xai_sdk")
    xai_sdk.chat = xai_chat
    xai_sdk.AsyncClient = MagicMock()

    sys.modules["xai_sdk"] = xai_sdk
    sys.modules["xai_sdk.chat"] = xai_chat
    sys.modules["xai_sdk.chat.chat_pb2"] = chat_pb2


_build_xai_sdk_stub()


class TestXAIFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for XAI (Grok) Chat and MultiAgent formatters.

    The formatter returns proto/mock objects rather than dicts, so assertions
    inspect mock attributes (role, args, kwargs, tool_calls, etc.).
    """

    async def asyncSetUp(self) -> None:
        self.msgs_system = [
            Msg(
                name="system",
                content="You're a helpful assistant.",
                role="system",
            ),
        ]

        self.msgs_conversation = [
            Msg(
                name="user",
                content="What is the capital of France?",
                role="user",
            ),
            Msg(
                name="assistant",
                content="The capital of France is Paris.",
                role="assistant",
            ),
            Msg(
                name="user",
                content="What is the capital of Germany?",
                role="user",
            ),
            Msg(
                name="assistant",
                content="The capital of Germany is Berlin.",
                role="assistant",
            ),
            Msg(
                name="user",
                content="What is the capital of Japan?",
                role="user",
            ),
        ]

        self.msgs_tools = [
            Msg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_1",
                        name="get_capital",
                        input='{"country": "Japan"}',
                    ),
                ],
                role="assistant",
            ),
            Msg(
                name="tool",
                content=[
                    ToolResultBlock(
                        id="call_1",
                        name="get_capital",
                        output=[
                            TextBlock(
                                type="text",
                                text="The capital of Japan is Tokyo.",
                            ),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                ],
                role="assistant",
            ),
            Msg(
                name="assistant",
                content="The capital of Japan is Tokyo.",
                role="assistant",
            ),
        ]

        self._hist_prompt = (
            XAIMultiAgentFormatter().conversation_history_prompt
        )

    # -------------------------------------------------------------------
    # XAIChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter_system_message(self) -> None:
        """System message becomes a system() proto."""
        fmt = XAIChatFormatter()
        res = await fmt.format(self.msgs_system)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].role, "system")
        self.assertIn("You're a helpful assistant.", res[0].args)

    async def test_chat_formatter_user_assistant(self) -> None:
        """User and assistant text messages are passed through correctly."""
        fmt = XAIChatFormatter()
        res = await fmt.format(self.msgs_conversation)
        roles = [m.role for m in res]
        self.assertEqual(roles.count("user"), 3)
        self.assertEqual(roles.count("assistant"), 2)

    async def test_chat_formatter_tool_call(self) -> None:
        """Assistant tool call becomes a _MessageProto with tool_calls set."""
        fmt = XAIChatFormatter()
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        # Find the tool call proto (has tool_calls list, role=ROLE_ASSISTANT=2)
        tool_call_msgs = [
            m
            for m in res
            if hasattr(m, "tool_calls") and len(m.tool_calls) > 0
        ]
        self.assertEqual(len(tool_call_msgs), 1)
        tc = tool_call_msgs[0].tool_calls[0]
        self.assertEqual(tc.id, "call_1")
        self.assertEqual(tc.function.name, "get_capital")
        self.assertIn('"country"', tc.function.arguments)

    async def test_chat_formatter_tool_result(self) -> None:
        """Tool result becomes a tool_result() proto with the right id."""
        fmt = XAIChatFormatter()
        res = await fmt.format(self.msgs_tools)
        tool_msgs = [m for m in res if m.role == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(
            tool_msgs[0].kwargs.get("tool_call_id"),
            "call_1",
        )
        self.assertIn(
            "The capital of Japan is Tokyo.",
            tool_msgs[0].args,
        )

    async def test_chat_formatter_thinking_dropped(self) -> None:
        """ThinkingBlock is silently ignored in user/assistant Grok
        messages."""
        fmt = XAIChatFormatter()
        msgs = [
            Msg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="inner thoughts"),
                    TextBlock(type="text", text="reply"),
                ],
                role="assistant",
            ),
        ]
        res = await fmt.format(msgs)
        # thinking block is silently skipped for assistant text path
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].role, "assistant")

    async def test_chat_formatter_empty(self) -> None:
        """Empty input returns empty list."""
        fmt = XAIChatFormatter()
        res = await fmt.format([])
        self.assertListEqual([], res)

    # -------------------------------------------------------------------
    # XAIMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter_system_message(self) -> None:
        """System message is passed through as a system() proto."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation],
        )
        self.assertEqual(res[0].role, "system")
        self.assertIn("You're a helpful assistant.", res[0].args)

    async def test_multiagent_formatter_conversation_history(self) -> None:
        """Non-tool agent messages are collapsed into a user() history
        proto."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(self.msgs_conversation)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].role, "user")
        hist_text = res[0].args[0]
        self.assertIn("<history>", hist_text)
        self.assertIn("</history>", hist_text)
        self.assertIn("user: What is the capital of France?", hist_text)
        self.assertIn("assistant: The capital of France is Paris.", hist_text)

    async def test_multiagent_formatter_first_group_has_hist_prompt(
        self,
    ) -> None:
        """First agent message group includes the conversation history
        prompt."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(self.msgs_conversation)
        hist_text = res[0].args[0]
        self.assertTrue(hist_text.startswith(self._hist_prompt))

    async def test_multiagent_formatter_full_history(self) -> None:
        """Full history produces system + conv_history + tool_call +
        tool_result + trailing."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        roles = [m.role for m in res]
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        self.assertIn("tool", roles)

    async def test_multiagent_formatter_tools_only_is_first(self) -> None:
        """When only tools are given, trailing agent uses is_first=True
        history."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(self.msgs_tools)
        # tool_call proto, tool_result proto, trailing user history
        trailing = [m for m in res if m.role == "user"]
        self.assertEqual(len(trailing), 1)
        hist_text = trailing[0].args[0]
        self.assertIn(self._hist_prompt, hist_text)
        self.assertIn("assistant: The capital of Japan is Tokyo.", hist_text)

    async def test_multiagent_formatter_nonfirst_trailing_no_hist_prompt(
        self,
    ) -> None:
        """Trailing agent after a conversation group uses is_first=False
        (no prompt)."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(
            [*self.msgs_conversation, *self.msgs_tools],
        )
        user_msgs = [m for m in res if m.role == "user"]
        # First is conversation history (is_first=True), last is trailing
        # (is_first=False)
        trailing_text = user_msgs[-1].args[0]
        self.assertNotIn(self._hist_prompt, trailing_text)
        self.assertIn("<history>", trailing_text)

    async def test_multiagent_formatter_empty(self) -> None:
        """Empty input returns empty list."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format([])
        self.assertListEqual([], res)
