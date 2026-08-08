# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for XAIChatFormatter and
XAIMultiAgentFormatter (xAI), following the reference test style.

Because these formatters return xai_sdk protobuf Message objects (not plain
dicts), a lightweight xai_sdk stub is built at module load so that tests run
without the real package.  The stub objects support __eq__ and __repr__ so
full assertListEqual comparisons work.
"""
import sys
from typing import Any
from types import ModuleType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from agentscope.formatter import XAIChatFormatter, XAIMultiAgentFormatter
from agentscope.message import (
    UserMsg,
    AssistantMsg,
    SystemMsg,
    TextBlock,
    DataBlock,
    Base64Source,
    ToolCallBlock,
    ToolResultBlock,
    ThinkingBlock,
    ToolResultState,
    HintBlock,
)


# ---------------------------------------------------------------------------
# Comparable stub objects for xai_sdk protobuf messages.
# ---------------------------------------------------------------------------


class _StubMessage:
    """Comparable stub for xai_sdk.chat.{user,assistant,system,tool_result}."""

    def __init__(
        self,
        role: str,
        args: tuple = (),
        kwargs: dict | None = None,
    ) -> None:
        self.role = role
        self.args = args
        self.kwargs = kwargs or {}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _StubMessage):
            return NotImplemented
        return (
            self.role == other.role
            and self.args == other.args
            and self.kwargs == other.kwargs
        )

    def __repr__(self) -> str:
        parts = [f"role={self.role!r}", f"args={self.args!r}"]
        if self.kwargs:
            parts.append(f"kwargs={self.kwargs!r}")
        return f"_StubMessage({', '.join(parts)})"


class _StubImage:
    """Comparable stub for xai_sdk.chat.image()."""

    def __init__(self, url: str, detail: str = "auto") -> None:
        self.type = "image"
        self.url = url
        self.detail = detail

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _StubImage):
            return NotImplemented
        return self.url == other.url and self.detail == other.detail

    def __repr__(self) -> str:
        return f"_StubImage(url={self.url!r}, detail={self.detail!r})"


def user(*args: Any) -> _StubMessage:
    """Create a comparable stub user message."""
    return _StubMessage(role="user", args=args)


def assistant(*args: Any) -> _StubMessage:
    """Create a comparable stub assistant message."""
    return _StubMessage(role="assistant", args=args)


def system(*args: Any) -> _StubMessage:
    """Create a comparable stub system message."""
    return _StubMessage(role="system", args=args)


def tool_result(*args: Any, **kwargs: Any) -> _StubMessage:
    """Create a comparable stub tool_result message."""
    return _StubMessage(role="tool", args=args, kwargs=kwargs)


def image(url: str, *, detail: str = "auto") -> _StubImage:
    """Create a comparable stub image object."""
    return _StubImage(url=url, detail=detail)


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
        def __init__(self) -> None:
            self.name = ""
            self.arguments = ""

    class _ToolCallProto:
        def __init__(self) -> None:
            self.id = ""
            self.type = 0
            self.function = _FunctionSpec()

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
    xai_chat.user = user
    xai_chat.assistant = assistant
    xai_chat.system = system
    xai_chat.tool_result = tool_result
    xai_chat.image = image

    xai_sdk = ModuleType("xai_sdk")
    xai_sdk.chat = xai_chat
    xai_sdk.AsyncClient = MagicMock()

    sys.modules["xai_sdk"] = xai_sdk
    sys.modules["xai_sdk.chat"] = xai_chat
    sys.modules["xai_sdk.chat.chat_pb2"] = chat_pb2


_build_xai_sdk_stub()


class TestXAIFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for XAI Chat and MultiAgent formatters.

    The stub objects support __eq__, so full assertListEqual works for
    user/assistant/system/tool_result messages.  Tool-call messages use
    _MessageProto which is checked via attribute assertions.
    """

    async def asyncSetUp(self) -> None:
        self.msgs_system = [
            SystemMsg(
                name="system",
                content="You're a helpful assistant.",
            ),
        ]

        self.msgs_conversation = [
            UserMsg(
                name="user",
                content="What is the capital of France?",
            ),
            AssistantMsg(
                name="assistant",
                content="The capital of France is Paris.",
            ),
            UserMsg(
                name="user",
                content="What is the capital of Germany?",
            ),
            AssistantMsg(
                name="assistant",
                content="The capital of Germany is Berlin.",
            ),
            UserMsg(
                name="user",
                content="What is the capital of Japan?",
            ),
        ]

        self.msgs_tools = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_1",
                        name="get_capital",
                        input='{"country": "Japan"}',
                    ),
                    ToolResultBlock(
                        id="call_1",
                        name="get_capital",
                        output=[
                            TextBlock(text="The capital of Japan is Tokyo."),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="The capital of Japan is Tokyo."),
                ],
            ),
        ]

        self._hist_prompt = (
            XAIMultiAgentFormatter().conversation_history_prompt
        )

    # -------------------------------------------------------------------
    # XAIChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter_system_message(self) -> None:
        """System message becomes a system() stub."""
        fmt = XAIChatFormatter()
        res = await fmt.format(self.msgs_system)
        self.assertListEqual(
            [system("You're a helpful assistant.")],
            res,
        )

    async def test_chat_formatter_user_assistant(self) -> None:
        """User and assistant text messages are passed through correctly."""
        fmt = XAIChatFormatter()
        res = await fmt.format(self.msgs_conversation)
        self.assertListEqual(
            [
                user("What is the capital of France?"),
                assistant("The capital of France is Paris."),
                user("What is the capital of Germany?"),
                assistant("The capital of Germany is Berlin."),
                user("What is the capital of Japan?"),
            ],
            res,
        )

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
        self.assertListEqual(
            [len(tool_call_msgs)],
            [1],
        )
        tc = tool_call_msgs[0].tool_calls[0]
        self.assertListEqual(
            [tc.id, tc.function.name, tc.function.arguments],
            ["call_1", "get_capital", '{"country": "Japan"}'],
        )

    async def test_chat_formatter_tool_result(self) -> None:
        """Tool result becomes a tool_result() stub with the right id."""
        fmt = XAIChatFormatter()
        res = await fmt.format(self.msgs_tools)
        tool_msgs = [m for m in res if m.role == "tool"]
        self.assertListEqual(
            tool_msgs,
            [
                tool_result(
                    "The capital of Japan is Tokyo.",
                    tool_call_id="call_1",
                ),
            ],
        )

    async def test_chat_formatter_thinking_dropped(self) -> None:
        """ThinkingBlock is silently ignored in user/assistant xAI
        messages."""
        fmt = XAIChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="inner thoughts"),
                    TextBlock(text="reply"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual([assistant("reply")], res)

    async def test_chat_formatter_empty(self) -> None:
        """Empty input returns empty list."""
        fmt = XAIChatFormatter()
        res = await fmt.format([])
        self.assertListEqual([], res)

    async def test_chat_formatter_image_detail_extra_param(self) -> None:
        """DataBlock detail extra param is forwarded to xAI image()."""
        fmt = XAIChatFormatter()
        image_b64 = "ZmFrZSBpbWFnZSBkYXRh"
        msgs = [
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="Inspect this image."),
                    DataBlock(
                        source=Base64Source(
                            data=image_b64,
                            media_type="image/png",
                        ),
                        detail="high",
                    ),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                user(
                    "Inspect this image.",
                    image(
                        f"data:image/png;base64,{image_b64}",
                        detail="high",
                    ),
                ),
            ],
            res,
        )

    # -------------------------------------------------------------------
    # XAIMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter_system_message(self) -> None:
        """System message is passed through as a system() stub."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation],
        )
        self.assertEqual(res[0], system("You're a helpful assistant."))

    async def test_multiagent_formatter_conversation_history(self) -> None:
        """Non-tool agent messages are collapsed into a user() history
        stub."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(self.msgs_conversation)
        self.assertListEqual(
            [
                user(
                    self._hist_prompt + "<history>\n"
                    "user: What is the capital of France?\n"
                    "assistant: The capital of France is Paris.\n"
                    "user: What is the capital of Germany?\n"
                    "assistant: The capital of Germany is Berlin.\n"
                    "user: What is the capital of Japan?\n"
                    "</history>",
                ),
            ],
            res,
        )

    async def test_multiagent_formatter_first_group_has_hist_prompt(
        self,
    ) -> None:
        """First agent message group includes the conversation history
        prompt."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(self.msgs_conversation)
        hist_text = res[0].args[0]
        self.assertListEqual(
            [hist_text.startswith(self._hist_prompt)],
            [True],
        )

    async def test_multiagent_formatter_full_history(self) -> None:
        """Full history produces system + conv_history + tool_call +
        tool_result + trailing assistant."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        roles = [m.role for m in res]
        self.assertListEqual(
            sorted(r for r in roles if isinstance(r, str)),
            sorted(["system", "user", "tool", "assistant"]),
        )

    async def test_multiagent_formatter_tools_only_is_first(self) -> None:
        """When only tools are given, trailing text is formatted as
        assistant."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(self.msgs_tools)
        trailing = [m for m in res if m.role == "assistant"]
        self.assertListEqual([len(trailing)], [1])

    async def test_multiagent_formatter_nonfirst_trailing_is_assistant(
        self,
    ) -> None:
        """Trailing text after a tool sequence is formatted as assistant."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format(
            [*self.msgs_conversation, *self.msgs_tools],
        )
        trailing = [m for m in res if m.role == "assistant"]
        self.assertListEqual([len(trailing)], [1])

    async def test_multiagent_formatter_empty(self) -> None:
        """Empty input returns empty list."""
        fmt = XAIMultiAgentFormatter()
        res = await fmt.format([])
        self.assertListEqual([], res)

    async def test_chat_formatter_complex_multi_step(self) -> None:
        """Complex multi-step sequence with interleaved thinking, text,
        tool calls, and tool results."""
        fmt = XAIChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="thinking_1"),
                    TextBlock(text="text_1"),
                    ToolCallBlock(
                        id="call_1",
                        name="func_1",
                        input='{"arg": "value1"}',
                    ),
                    ToolCallBlock(
                        id="call_2",
                        name="func_2",
                        input='{"arg": "value2"}',
                    ),
                    ToolResultBlock(
                        id="call_1",
                        name="func_1",
                        output=[TextBlock(text="result_1")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ToolResultBlock(
                        id="call_2",
                        name="func_2",
                        output=[TextBlock(text="result_2")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ThinkingBlock(thinking="thinking_2"),
                    TextBlock(text="text_2"),
                    ToolCallBlock(
                        id="call_3",
                        name="func_3",
                        input='{"arg": "value3"}',
                    ),
                    ToolResultBlock(
                        id="call_3",
                        name="func_3",
                        output=[TextBlock(text="result_3")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ToolCallBlock(
                        id="call_4",
                        name="func_4",
                        input='{"arg": "value4"}',
                    ),
                    ToolResultBlock(
                        id="call_4",
                        name="func_4",
                        output=[TextBlock(text="result_4")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ThinkingBlock(thinking="thinking_3"),
                    TextBlock(text="text_3"),
                ],
            ),
        ]
        res = await fmt.format(msgs)

        # First message: proto with text_1 content + 2 tool_calls
        self.assertTrue(hasattr(res[0], "tool_calls"))
        self.assertEqual(len(res[0].tool_calls), 2)
        self.assertEqual(len(res[0].content), 1)
        self.assertEqual(res[0].tool_calls[0].id, "call_1")
        self.assertEqual(res[0].tool_calls[0].function.name, "func_1")
        self.assertEqual(
            res[0].tool_calls[0].function.arguments,
            '{"arg": "value1"}',
        )
        self.assertEqual(res[0].tool_calls[1].id, "call_2")
        self.assertEqual(res[0].tool_calls[1].function.name, "func_2")
        self.assertEqual(
            res[0].tool_calls[1].function.arguments,
            '{"arg": "value2"}',
        )

        # Tool results
        self.assertEqual(
            res[1],
            tool_result("result_1", tool_call_id="call_1"),
        )
        self.assertEqual(
            res[2],
            tool_result("result_2", tool_call_id="call_2"),
        )

        # Second proto: text_2 + 1 tool_call
        self.assertTrue(hasattr(res[3], "tool_calls"))
        self.assertEqual(len(res[3].tool_calls), 1)
        self.assertEqual(len(res[3].content), 1)
        self.assertEqual(res[3].tool_calls[0].id, "call_3")
        self.assertEqual(res[3].tool_calls[0].function.name, "func_3")

        # Tool result for call_3
        self.assertEqual(
            res[4],
            tool_result("result_3", tool_call_id="call_3"),
        )

        # Third proto: no content + 1 tool_call
        self.assertTrue(hasattr(res[5], "tool_calls"))
        self.assertEqual(len(res[5].tool_calls), 1)
        self.assertEqual(len(res[5].content), 0)
        self.assertEqual(res[5].tool_calls[0].id, "call_4")
        self.assertEqual(res[5].tool_calls[0].function.name, "func_4")

        # Tool result for call_4
        self.assertEqual(
            res[6],
            tool_result("result_4", tool_call_id="call_4"),
        )

        # Final assistant text
        self.assertEqual(res[7], assistant("text_3"))

        # Total 8 items
        self.assertEqual(len(res), 8)

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = XAIChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    TextBlock(text="Let me think about that."),
                    HintBlock(hint="Remember to be concise."),
                    TextBlock(text="Here is my answer."),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                assistant("Let me think about that."),
                user("Remember to be concise."),
                assistant("Here is my answer."),
            ],
            res,
        )

    async def test_chat_formatter_hint_block_multimodal(self) -> None:
        """Multimodal HintBlock becomes one user() stub carrying text +
        image."""
        fmt = XAIChatFormatter()
        image_b64 = "ZmFrZSBpbWFnZSBkYXRh"
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    HintBlock(
                        hint=[
                            TextBlock(text="Inspect this screenshot:"),
                            DataBlock(
                                source=Base64Source(
                                    data=image_b64,
                                    media_type="image/png",
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                user(
                    "Inspect this screenshot:",
                    image(f"data:image/png;base64,{image_b64}"),
                ),
            ],
            res,
        )
