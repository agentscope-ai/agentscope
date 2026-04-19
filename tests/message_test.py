# -*- coding: utf-8 -*-
"""Test cases for the message module."""
import json
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString

from agentscope.message import (
    UserMsg,
    AssistantMsg,
    SystemMsg,
    Msg,
    TextBlock,
    ThinkingBlock,
    HintBlock,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    URLSource,
    Base64Source,
)


class UserMsgCreationTest(IsolatedAsyncioTestCase):
    """Test cases for UserMsg creation with various content types."""

    async def test_string_content(self) -> None:
        """UserMsg with plain string content."""
        msg = UserMsg(name="user", content="hello world")
        self.assertDictEqual(
            msg.model_dump(),
            {
                "id": AnyString(),
                "name": "user",
                "role": "user",
                "content": "hello world",
                "metadata": {},
                "created_at": AnyString(),
            },
        )

    async def test_empty_string_content(self) -> None:
        """Empty string is still valid content."""
        msg = UserMsg(name="user", content="")
        self.assertEqual(msg.content, "")
        self.assertEqual(msg.role, "user")

    async def test_text_blocks(self) -> None:
        """UserMsg with a list of TextBlocks."""
        msg = UserMsg(
            name="user",
            content=[TextBlock(text="1"), TextBlock(text="2")],
        )
        self.assertDictEqual(
            msg.model_dump(),
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

    async def test_url_data_block(self) -> None:
        """UserMsg with a DataBlock using URLSource."""
        msg = UserMsg(
            name="user",
            content=[
                TextBlock(text="describe this"),
                DataBlock(
                    source=URLSource(
                        url="https://example.com/image.png",
                        media_type="image/png",
                    ),
                ),
            ],
        )
        dumped = msg.model_dump()
        self.assertEqual(dumped["content"][1]["type"], "data")
        self.assertEqual(
            dumped["content"][1]["source"]["url"],
            "https://example.com/image.png",
        )
        self.assertEqual(
            dumped["content"][1]["source"]["media_type"],
            "image/png",
        )

    async def test_base64_data_block(self) -> None:
        """DataBlock with Base64Source stores data correctly."""
        b64_data = "iVBORw0KGgoAAAANSUhEUgAAAAUA"
        msg = UserMsg(
            name="user",
            content=[
                DataBlock(
                    source=Base64Source(
                        data=b64_data,
                        media_type="image/png",
                    ),
                ),
            ],
        )
        dumped = msg.model_dump()
        self.assertEqual(dumped["content"][0]["source"]["data"], b64_data)
        self.assertEqual(dumped["content"][0]["source"]["type"], "base64")

    async def test_named_data_block(self) -> None:
        """DataBlock optional name field is preserved."""
        msg = UserMsg(
            name="user",
            content=[
                DataBlock(
                    name="screenshot",
                    source=Base64Source(data="abc", media_type="image/png"),
                ),
            ],
        )
        self.assertEqual(msg.model_dump()["content"][0]["name"], "screenshot")

    async def test_data_block_name_defaults_to_none(self) -> None:
        """DataBlock name is None when not provided."""
        msg = UserMsg(
            name="user",
            content=[
                DataBlock(
                    source=Base64Source(data="abc", media_type="image/png"),
                ),
            ],
        )
        self.assertIsNone(msg.model_dump()["content"][0]["name"])

    async def test_metadata(self) -> None:
        """Metadata dict is stored and accessible."""
        msg = UserMsg(
            name="user",
            content="hi",
            metadata={"session_id": "abc123"},
        )
        self.assertEqual(msg.metadata, {"session_id": "abc123"})

    async def test_custom_created_at(self) -> None:
        """Custom created_at timestamp is preserved."""
        ts = "2024-01-01T00:00:00"
        msg = UserMsg(name="user", content="hi", created_at=ts)
        self.assertEqual(msg.created_at, ts)


class AssistantMsgCreationTest(IsolatedAsyncioTestCase):
    """Test cases for AssistantMsg creation with various block types."""

    async def test_string_content(self) -> None:
        msg = AssistantMsg(name="bot", content="hello")
        self.assertEqual(msg.role, "assistant")
        self.assertEqual(msg.content, "hello")

    async def test_thinking_block(self) -> None:
        """ThinkingBlock is only allowed in assistant messages."""
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

    async def test_hint_block(self) -> None:
        msg = AssistantMsg(
            name="assistant",
            content=[HintBlock(hint="hint...")],
        )
        self.assertEqual(msg.model_dump()["content"][0]["type"], "hint")
        self.assertEqual(msg.model_dump()["content"][0]["hint"], "hint...")

    async def test_tool_call_block(self) -> None:
        """ToolCallBlock defaults await_user_confirmation to False."""
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

    async def test_tool_call_block_with_confirmation(self) -> None:
        """ToolCallBlock with await_user_confirmation set to True."""
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

    async def test_tool_result_string_output(self) -> None:
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

    async def test_tool_result_list_output(self) -> None:
        """ToolResultBlock output can be a list of TextBlock and DataBlock."""
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

    async def test_all_tool_result_states(self) -> None:
        """ToolResultBlock supports success, error, interrupted, running."""
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

    async def test_mixed_blocks(self) -> None:
        """Thinking + text + tool_call in a single assistant message."""
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="let me think"),
                TextBlock(text="the answer is 42"),
                ToolCallBlock(id="c1", name="calc", input="{}"),
            ],
        )
        types = [b["type"] for b in msg.model_dump()["content"]]
        self.assertEqual(types, ["thinking", "text", "tool_call"])


class SystemMsgCreationTest(IsolatedAsyncioTestCase):
    """Test cases for SystemMsg creation."""

    async def test_string_content(self) -> None:
        msg = SystemMsg(name="system", content="You are a helpful assistant.")
        self.assertEqual(msg.role, "system")
        self.assertEqual(msg.content, "You are a helpful assistant.")

    async def test_text_block(self) -> None:
        """SystemMsg can contain a list of TextBlocks."""
        msg = SystemMsg(
            name="system",
            content=[TextBlock(text="Be concise.")],
        )
        self.assertEqual(msg.model_dump()["content"][0]["type"], "text")


class UserMsgValidationTest(IsolatedAsyncioTestCase):
    """User messages can only contain text and data blocks. Anything else
    should raise ValueError."""

    async def test_rejects_thinking_block(self) -> None:
        with self.assertRaises(ValueError):
            Msg(
                name="user",
                role="user",
                content=[ThinkingBlock(thinking="thinking...")],
            )

    async def test_rejects_hint_block(self) -> None:
        with self.assertRaises(ValueError):
            Msg(
                name="user",
                role="user",
                content=[HintBlock(hint="hint...")],
            )

    async def test_rejects_tool_call_block(self) -> None:
        with self.assertRaises(ValueError):
            Msg(
                name="user",
                role="user",
                content=[ToolCallBlock(id="1", name="tool", input="{}")],
            )

    async def test_rejects_tool_result_block(self) -> None:
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

    async def test_mixed_valid_and_invalid_still_rejected(self) -> None:
        """Even if TextBlock is present, an invalid block should still cause
        rejection."""
        with self.assertRaises(ValueError):
            Msg(
                name="user",
                role="user",
                content=[
                    TextBlock(text="hi"),
                    ThinkingBlock(thinking="nope"),
                ],
            )


class SystemMsgValidationTest(IsolatedAsyncioTestCase):
    """System messages can only contain text blocks."""

    async def test_rejects_data_block(self) -> None:
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

    async def test_rejects_thinking_block(self) -> None:
        with self.assertRaises(ValueError):
            Msg(
                name="system",
                role="system",
                content=[ThinkingBlock(thinking="thinking...")],
            )

    async def test_rejects_hint_block(self) -> None:
        with self.assertRaises(ValueError):
            Msg(
                name="system",
                role="system",
                content=[HintBlock(hint="hint...")],
            )

    async def test_rejects_tool_call_block(self) -> None:
        with self.assertRaises(ValueError):
            Msg(
                name="system",
                role="system",
                content=[ToolCallBlock(id="1", name="tool", input="{}")],
            )

    async def test_rejects_tool_result_block(self) -> None:
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


class MsgGetTextContentTest(IsolatedAsyncioTestCase):
    """Test cases for Msg.get_text_content."""

    async def test_from_string(self) -> None:
        """String content is returned as-is."""
        msg = UserMsg(name="user", content="hello")
        self.assertEqual(msg.get_text_content(), "hello")

    async def test_from_text_blocks(self) -> None:
        """Multiple TextBlocks joined with default separator."""
        msg = UserMsg(
            name="user",
            content=[TextBlock(text="foo"), TextBlock(text="bar")],
        )
        self.assertEqual(msg.get_text_content(), "foo\nbar")

    async def test_custom_separator(self) -> None:
        msg = UserMsg(
            name="user",
            content=[TextBlock(text="foo"), TextBlock(text="bar")],
        )
        self.assertEqual(msg.get_text_content(separator=" | "), "foo | bar")

    async def test_ignores_non_text_blocks(self) -> None:
        """Only TextBlocks contribute to the result."""
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="internal"),
                TextBlock(text="visible"),
            ],
        )
        self.assertEqual(msg.get_text_content(), "visible")

    async def test_returns_none_when_no_text(self) -> None:
        msg = AssistantMsg(
            name="assistant",
            content=[ThinkingBlock(thinking="only thinking")],
        )
        self.assertIsNone(msg.get_text_content())


class MsgGetContentBlocksTest(IsolatedAsyncioTestCase):
    """Test cases for Msg.get_content_blocks."""

    async def test_string_wrapped_to_text_block(self) -> None:
        """String content is converted to a single TextBlock."""
        msg = UserMsg(name="user", content="hello")
        blocks = msg.get_content_blocks()
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].type, "text")
        self.assertEqual(
            blocks[0].text,  # type: ignore[attr-defined]
            "hello",
        )

    async def test_no_filter_returns_all(self) -> None:
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="t"),
                TextBlock(text="x"),
            ],
        )
        self.assertEqual(len(msg.get_content_blocks()), 2)

    async def test_filter_by_single_type(self) -> None:
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="t"),
                TextBlock(text="a"),
                TextBlock(text="b"),
            ],
        )
        self.assertEqual(len(msg.get_content_blocks("text")), 2)
        self.assertEqual(len(msg.get_content_blocks("thinking")), 1)

    async def test_filter_by_type_list(self) -> None:
        """Passing a list of types returns the union."""
        msg = AssistantMsg(
            name="assistant",
            content=[
                ThinkingBlock(thinking="t"),
                TextBlock(text="a"),
                HintBlock(hint="h"),
            ],
        )
        blocks = msg.get_content_blocks(["text", "hint"])
        self.assertEqual(len(blocks), 2)
        types = {b.type for b in blocks}
        self.assertSetEqual(types, {"text", "hint"})

    async def test_no_match_returns_empty(self) -> None:
        msg = UserMsg(name="user", content=[TextBlock(text="hi")])
        self.assertEqual(len(msg.get_content_blocks("thinking")), 0)


class MsgHasContentBlocksTest(IsolatedAsyncioTestCase):
    """Test cases for Msg.has_content_blocks."""

    async def test_with_matching_type(self) -> None:
        msg = AssistantMsg(
            name="a",
            content=[ThinkingBlock(thinking="t"), TextBlock(text="x")],
        )
        self.assertTrue(msg.has_content_blocks())
        self.assertTrue(msg.has_content_blocks("thinking"))
        self.assertTrue(msg.has_content_blocks("text"))

    async def test_with_non_matching_type(self) -> None:
        msg = UserMsg(name="user", content=[TextBlock(text="hi")])
        self.assertFalse(msg.has_content_blocks("thinking"))

    async def test_string_content_counts_as_text(self) -> None:
        msg = UserMsg(name="user", content="hello")
        self.assertTrue(msg.has_content_blocks())
        self.assertTrue(msg.has_content_blocks("text"))
        self.assertFalse(msg.has_content_blocks("thinking"))


class MsgSerializationTest(IsolatedAsyncioTestCase):
    """Test cases for serialization round-trips."""

    async def test_round_trip_string_content(self) -> None:
        """model_dump -> model_validate should preserve all fields."""
        original = UserMsg(name="user", content="hello")
        restored = Msg.model_validate(original.model_dump())
        self.assertEqual(restored.content, original.content)
        self.assertEqual(restored.role, original.role)
        self.assertEqual(restored.name, original.name)

    async def test_round_trip_mixed_blocks(self) -> None:
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
        self.assertEqual(len(restored.get_content_blocks()), 3)
        self.assertEqual(
            restored.get_content_blocks("thinking")[0].thinking,  # type: ignore[attr-defined]
            "hmm",
        )
        self.assertEqual(
            restored.get_content_blocks("text")[0].text,  # type: ignore[attr-defined]
            "answer",
        )

    async def test_to_json(self) -> None:
        msg = UserMsg(name="user", content="hello")
        parsed = json.loads(msg.model_dump_json())
        self.assertEqual(parsed["content"], "hello")
        self.assertEqual(parsed["role"], "user")

    async def test_tool_result_list_output_round_trip(self) -> None:
        """ToolResultBlock with list output survives serialization."""
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
        result_block = restored.get_content_blocks("tool_result")[0]
        self.assertIsInstance(
            result_block.output,  # type: ignore[union-attr]
            list,
        )
        self.assertEqual(
            result_block.output[0].text,  # type: ignore[union-attr, index]
            "file content",
        )

    async def test_id_preserved_after_round_trip(self) -> None:
        """Message id should not change during serialization."""
        original = UserMsg(name="user", content="test")
        restored = Msg.model_validate(original.model_dump())
        self.assertEqual(restored.id, original.id)