# -*- coding: utf-8 -*-
"""A template test case."""
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
    HintBlock,
    Msg,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    Usage,
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
                "content": [
                    {
                        "id": AnyString(),
                        "text": "hello world",
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "usage": None,
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
                    {
                        "type": "text",
                        "text": "1",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "text",
                        "text": "2",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "usage": None,
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
                    {
                        "type": "text",
                        "text": "1",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "url",
                            "url": "https://example.com/image.png",
                            "media_type": "image/png",
                        },
                        "name": None,
                        "created_at": AnyString(),
                        "finished_at": None,
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
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "usage": None,
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
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "usage": None,
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
                    {
                        "type": "hint",
                        "hint": "hint...",
                        "id": AnyString(),
                        "source": None,
                        "created_at": AnyString(),
                        "finished_at": AnyString(),
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "usage": None,
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
                        state=ToolResultState.SUCCESS,
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
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            )

    async def test_append_usage_keeps_a_copy(self) -> None:
        """The message accumulates a copy of the usage it is given."""
        first = Usage(
            input_tokens=10,
            output_tokens=5,
            cache_input_tokens=2,
            cache_creation_input_tokens=1,
        )
        second = Usage(input_tokens=7, output_tokens=3, cache_input_tokens=4)
        msg = AssistantMsg(name="agent", content=[])
        msg.append_usage(first)
        msg.append_usage(second)
        accumulated = msg.usage
        assert accumulated is not None

        self.assertDictEqual(
            {
                "accumulated": accumulated.model_dump(),
                "first": first.model_dump(),
                "second": second.model_dump(),
            },
            {
                "accumulated": {
                    "input_tokens": 17,
                    "output_tokens": 8,
                    "cache_input_tokens": 6,
                    "cache_creation_input_tokens": 1,
                },
                # Each model call keeps its own usage, so it can still be
                # reported or accumulated into other messages afterwards.
                "first": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_input_tokens": 2,
                    "cache_creation_input_tokens": 1,
                },
                "second": {
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "cache_input_tokens": 4,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    async def test_append_usage_does_not_share_one_object(self) -> None:
        """Messages given the same usage stay independent."""
        shared = Usage(input_tokens=4, output_tokens=2)
        first = AssistantMsg(name="agent_a", content=[])
        second = AssistantMsg(name="agent_b", content=[])
        first.append_usage(shared)
        second.append_usage(shared)
        first.append_usage(Usage(input_tokens=1, output_tokens=1))
        first_usage = first.usage
        second_usage = second.usage
        assert first_usage is not None and second_usage is not None

        self.assertDictEqual(
            {
                "first": first_usage.model_dump(),
                "second": second_usage.model_dump(),
            },
            {
                "first": {
                    "input_tokens": 5,
                    "output_tokens": 3,
                    "cache_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "second": {
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "cache_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        )
