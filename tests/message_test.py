# -*- coding: utf-8 -*-
"""A template test case."""
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import (
    UserMsg,
    TextBlock,
    DataBlock,
    URLSource,
    Base64Source,
    ThinkingBlock,
    AssistantMsg,
    HintBlock,
    Msg,
    ToolCallBlock,
    ToolResultBlock,
)
from utils import AnyString


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
