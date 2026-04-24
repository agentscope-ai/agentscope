# -*- coding: utf-8 -*-
"""A template test case."""
import json
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString

from agentscope.message import (
    UserMsg,
    TextBlock,
    DataBlock,
    URLSource,
    Base64Source,
    ThinkingBlock,
    AssistantMsg,
    SystemMsg,
    HintBlock,
    Msg,
    ToolCallBlock,
    ToolResultBlock,
)


class MessageTest(IsolatedAsyncioTestCase):
    """The template test case."""

    async def test_creating_message(self) -> None:
        """The template test."""
        # Test string content
        user_msg = UserMsg(name="user", content="hello world")
        self.assertDictEqual(
            user_msg.model_dump(),
            {
                "id": AnyString(),
                "name": "user",
                "role": "user",
                "content": "hello world",
                "metadata": {},
                "created_at": AnyString(),
            },
        )

        # Test list of content
        user_msg = UserMsg(
            name="user",
            content=[TextBlock(text="1"), TextBlock(text="2")],
        )
        self.assertDictEqual(
            user_msg.model_dump(),
            {
                "id": AnyString(),
                "name": "user",
                "role": "user",
                "content": [
                    {"type": "text", "text": "1", "id": AnyString()},
                    {"type": "text", "text": "2", "id": AnyString()},
                ],
                "metadata": {},
                "created_at": AnyString(),
            },
        )

        # Test DataBlock content
        user_msg = UserMsg(
            name="user",
            content=[
                TextBlock(text="1"),
                DataBlock(
                    source=URLSource(
                        url="https://example.com/image.png",
                        media_type="image/png",
                    ),
                ),
                DataBlock(
                    source=Base64Source(
                        data="iVBORw0KGgoAAAANSUhEUgAAAAUA",
                        media_type="image/png",
                    ),
                ),
            ],
        )

        self.assertDictEqual(
            user_msg.model_dump(),
            {
                "id": AnyString(),
                "name": "user",
                "role": "user",
                "content": [
                    {"type": "text", "text": "1", "id": AnyString()},
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "url",
                            "url": "https://example.com/image.png",
                            "media_type": "image/png",
                        },
                        "name": None,
                    },
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "base64",
                            "data": "iVBORw0KGgoAAAANSUhEUgAAAAUA",
                            "media_type": "image/png",
                        },
                        "name": None,
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
            },
        )

        # Test thinking content
        msg = AssistantMsg(
            name="assistant",
            content=[ThinkingBlock(thinking="thinking...")],
        )
        self.assertDictEqual(
            msg.model_dump(),
            {
                "id": AnyString(),
                "name": "assistant",
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "thinking...",
                        "id": AnyString(),
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
            },
        )

        # Test hint content
        msg = AssistantMsg(
            name="assistant",
            content=[HintBlock(hint="hint...")],
        )
        self.assertDictEqual(
            msg.model_dump(),
            {
                "id": AnyString(),
                "name": "assistant",
                "role": "assistant",
                "content": [
                    {"type": "hint", "hint": "hint...", "id": AnyString()},
                ],
                "metadata": {},
                "created_at": AnyString(),
            },
        )

    async def test_invalid_message(self) -> None:
        """Test invalid message creation."""
        # User message with thinking block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="user",
                role="user",
                content=[ThinkingBlock(thinking="thinking...")],
            )

        # User message with hint block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="user",
                role="user",
                content=[HintBlock(hint="hint...")],
            )

        # User message with tool call block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="user",
                role="user",
                content=[ToolCallBlock(id="1", name="tool", input="{}")],
            )

        # User message with tool result block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="user",
                role="user",
                content=[
                    ToolResultBlock(
                        id="1",
                        name="tool",
                        output="result",
                        state="success",
                    ),
                ],
            )

        # System message with data block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="system",
                role="system",
                content=[
                    DataBlock(
                        source=URLSource(
                            url="https://example.com/image.png",
                            media_type="image/png",
                        ),
                    ),
                ],
            )

        # System message with thinking block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="system",
                role="system",
                content=[ThinkingBlock(thinking="thinking...")],
            )

        # System message with hint block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="system",
                role="system",
                content=[HintBlock(hint="hint...")],
            )

        # System message with tool call block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="system",
                role="system",
                content=[ToolCallBlock(id="1", name="tool", input="{}")],
            )

        # System message with tool result block should raise ValueError
        with self.assertRaises(ValueError):
            Msg(
                name="system",
                role="system",
                content=[
                    ToolResultBlock(
                        id="1",
                        name="tool",
                        output="result",
                        state="success",
                    ),
                ],
            )

    async def test_user_msg_edge_cases(self) -> None:
        """Test UserMsg edge cases not covered by the template."""
        # Empty string is still valid content
        msg = UserMsg(name="user", content="")
        self.assertEqual(msg.content, "")
        self.assertEqual(msg.role, "user")

        # DataBlock with named field
        msg = UserMsg(
            name="user",
            content=[
                DataBlock(
                    name="screenshot",
                    source=Base64Source(
                        data="abc",
                        media_type="image/png",
                    ),
                ),
            ],
        )
        self.assertEqual(
            msg.model_dump()["content"][0]["name"],
            "screenshot",
        )

        # DataBlock name defaults to None
        msg = UserMsg(
            name="user",
            content=[
                DataBlock(
                    source=Base64Source(
                        data="abc",
                        media_type="image/png",
                    ),
                ),
            ],
        )
        self.assertIsNone(msg.model_dump()["content"][0]["name"])

        # Custom metadata
        msg = UserMsg(
            name="user",
            content="hi",
            metadata={"session_id": "abc123"},
        )
        self.assertEqual(msg.metadata, {"session_id": "abc123"})

        # Custom created_at timestamp
        ts = "2024-01-01T00:00:00"
        msg = UserMsg(name="user", content="hi", created_at=ts)
        self.assertEqual(msg.created_at, ts)

    async def test_assistant_msg_blocks(self) -> None:
        """Test AssistantMsg with tool call and tool result blocks."""
        # ToolCallBlock defaults await_user_confirmation to False
        msg = AssistantMsg(
            name="assistant",
            content=[
                ToolCallBlock(
                    id="call_1",
                    name="get_weather",
                    input='{"city": "Beijing"}',
                ),
            ],
        )
        block = msg.model_dump()["content"][0]
        self.assertEqual(block["type"], "tool_call")
        self.assertEqual(block["name"], "get_weather")
        self.assertFalse(block["await_user_confirmation"])

        # ToolCallBlock with await_user_confirmation enabled
        msg = AssistantMsg(
            name="assistant",
            content=[
                ToolCallBlock(
                    id="call_1",
                    name="rm_file",
                    input='{"path": "/tmp/x"}',
                    await_user_confirmation=True,
                ),
            ],
        )
        block = msg.model_dump()["content"][0]
        self.assertTrue(block["await_user_confirmation"])

        # ToolResultBlock with string output
        msg = AssistantMsg(
            name="assistant",
            content=[
                ToolResultBlock(
                    id="call_1",
                    name="get_weather",
                    output="sunny",
                    state="success",
                ),
            ],
        )
        block = msg.model_dump()["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["state"], "success")
        self.assertEqual(block["output"], "sunny")

        # ToolResultBlock with list output
        msg = AssistantMsg(
            name="assistant",
            content=[
                ToolResultBlock(
                    id="call_1",
                    name="screenshot",
                    output=[
                        TextBlock(text="here is the screenshot"),
                        DataBlock(
                            source=Base64Source(
                                data="abc",
                                media_type="image/png",
                            ),
                        ),
                    ],
                    state="success",
                ),
            ],
        )
        output = msg.model_dump()["content"][0]["output"]
        self.assertEqual(len(output), 2)
        self.assertEqual(output[0]["type"], "text")
        self.assertEqual(output[1]["type"], "data")

        # ToolResultBlock supports all four execution states
        for state in ("success", "error", "interrupted", "running"):
            msg = AssistantMsg(
                name="assistant",
                content=[
                    ToolResultBlock(
                        id="x",
                        name="tool",
                        output="out",
                        state=state,  # type: ignore[arg-type]
                    ),
                ],
            )
            self.assertEqual(
                msg.model_dump()["content"][0]["state"],
                state,
            )

        # Mixed blocks in one assistant message
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="let me think"),
                TextBlock(text="the answer is 42"),
                ToolCallBlock(id="c1", name="calc", input="{}"),
            ],
        )
        types = [b["type"] for b in msg.model_dump()["content"]]
        self.assertEqual(
            types,
            ["thinking", "text", "tool_call"],
        )

    async def test_system_msg_creation(self) -> None:
        """Test SystemMsg creation with various content types."""
        # String content
        msg = SystemMsg(
            name="system",
            content="You are a helpful assistant.",
        )
        self.assertEqual(msg.role, "system")
        self.assertEqual(
            msg.content,
            "You are a helpful assistant.",
        )

        # TextBlock content
        msg = SystemMsg(
            name="system",
            content=[TextBlock(text="Be concise.")],
        )
        self.assertEqual(
            msg.model_dump()["content"][0]["type"],
            "text",
        )

    async def test_get_text_content(self) -> None:
        """Test Msg.get_text_content with various scenarios."""
        # String content is returned as-is
        msg = UserMsg(name="user", content="hello")
        self.assertEqual(msg.get_text_content(), "hello")

        # Multiple TextBlocks joined with default separator
        msg = UserMsg(
            name="user",
            content=[
                TextBlock(text="foo"),
                TextBlock(text="bar"),
            ],
        )
        self.assertEqual(msg.get_text_content(), "foo\nbar")

        # Custom separator
        self.assertEqual(
            msg.get_text_content(separator=" | "),
            "foo | bar",
        )

        # Non-text blocks are ignored
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="internal"),
                TextBlock(text="visible"),
            ],
        )
        self.assertEqual(msg.get_text_content(), "visible")

        # Returns None when no TextBlock exists
        msg = AssistantMsg(
            name="assistant",
            content=[ThinkingBlock(thinking="only thinking")],
        )
        self.assertIsNone(msg.get_text_content())

    async def test_get_content_blocks(self) -> None:
        """Test Msg.get_content_blocks with various scenarios."""
        # String content is converted to a single TextBlock
        msg = UserMsg(name="user", content="hello")
        blocks = list(msg.get_content_blocks())
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].type, "text")
        self.assertEqual(
            blocks[0].text,  # type: ignore[attr-defined]
            "hello",
        )

        # Without filter all blocks are returned
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="t"),
                TextBlock(text="x"),
            ],
        )
        self.assertEqual(len(msg.get_content_blocks()), 2)

        # Filtering by one type
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="t"),
                TextBlock(text="a"),
                TextBlock(text="b"),
            ],
        )
        self.assertEqual(
            len(msg.get_content_blocks("text")),
            2,
        )
        self.assertEqual(
            len(msg.get_content_blocks("thinking")),
            1,
        )

        # Filtering by a list of types
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="t"),
                TextBlock(text="a"),
                HintBlock(hint="h"),
            ],
        )
        block_types = ["text", "hint"]
        blocks = list(
            msg.get_content_blocks(
                block_types,
            ),  # type: ignore[arg-type]
        )
        self.assertEqual(len(blocks), 2)
        types = {b.type for b in blocks}
        self.assertSetEqual(types, {"text", "hint"})

        # Empty list when no block matches the type
        msg = UserMsg(
            name="user",
            content=[TextBlock(text="hi")],
        )
        self.assertEqual(
            len(msg.get_content_blocks("thinking")),
            0,
        )

    async def test_has_content_blocks(self) -> None:
        """Test Msg.has_content_blocks with various scenarios."""
        # Returns True when matching blocks exist
        msg = AssistantMsg(
            name="a",
            content=[
                ThinkingBlock(thinking="t"),
                TextBlock(text="x"),
            ],
        )
        self.assertTrue(msg.has_content_blocks())
        self.assertTrue(msg.has_content_blocks("thinking"))
        self.assertTrue(msg.has_content_blocks("text"))

        # Returns False when no matching blocks exist
        msg = UserMsg(
            name="user",
            content=[TextBlock(text="hi")],
        )
        self.assertFalse(msg.has_content_blocks("thinking"))

        # String content is treated as having a text block
        msg = UserMsg(name="user", content="hello")
        self.assertTrue(msg.has_content_blocks())
        self.assertTrue(msg.has_content_blocks("text"))
        self.assertFalse(msg.has_content_blocks("thinking"))

    async def test_serialization(self) -> None:
        """Test Msg serialization and deserialization."""
        # model_dump -> model_validate preserves all fields
        original = UserMsg(name="user", content="hello")
        restored = Msg.model_validate(original.model_dump())
        self.assertEqual(restored.content, original.content)
        self.assertEqual(restored.role, original.role)
        self.assertEqual(restored.name, original.name)

        # Mixed blocks survive round-trip
        original = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="hmm"),
                TextBlock(text="answer"),
                ToolCallBlock(
                    id="c1",
                    name="search",
                    input='{"q": "test"}',
                ),
            ],
        )
        restored = Msg.model_validate(original.model_dump())
        self.assertEqual(
            len(restored.get_content_blocks()),
            3,
        )
        thinking = list(
            restored.get_content_blocks("thinking"),
        )
        text = list(restored.get_content_blocks("text"))
        self.assertEqual(
            thinking[0].thinking,  # type: ignore[attr-defined]
            "hmm",
        )
        self.assertEqual(
            text[0].text,  # type: ignore[attr-defined]
            "answer",
        )

        # model_dump_json produces valid JSON
        msg = UserMsg(name="user", content="hello")
        parsed = json.loads(msg.model_dump_json())
        self.assertEqual(parsed["content"], "hello")
        self.assertEqual(parsed["role"], "user")

        # ToolResultBlock with list output survives round-trip
        original = AssistantMsg(
            name="assistant",
            content=[
                ToolResultBlock(
                    id="r1",
                    name="read_file",
                    output=[TextBlock(text="file content")],
                    state="success",
                ),
            ],
        )
        restored = Msg.model_validate(original.model_dump())
        result_blocks = list(
            restored.get_content_blocks("tool_result"),
        )
        result_block = result_blocks[0]
        self.assertIsInstance(
            result_block.output,  # type: ignore[union-attr]
            list,
        )
        self.assertEqual(
            result_block.output[0].text,  # type: ignore[union-attr, index]
            "file content",
        )

        # Message id should not change during serialization
        original = UserMsg(name="user", content="test")
        restored = Msg.model_validate(original.model_dump())
        self.assertEqual(restored.id, original.id)
