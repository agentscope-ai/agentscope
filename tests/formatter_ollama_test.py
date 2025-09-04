# -*- coding: utf-8 -*-
"""The Ollama formatter unittests."""
import os
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.formatter import OllamaChatFormatter, OllamaMultiAgentFormatter
from agentscope.message import (
    Msg,
    URLSource,
    TextBlock,
    ImageBlock,
    ToolUseBlock,
    ToolResultBlock,
)


class TestOllamaFormatter(IsolatedAsyncioTestCase):
    """Unittest for the Ollama formatter."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.image_path = "./image.png"
        with open(self.image_path, "wb") as f:
            f.write(b"fake image content")

        self.msgs_system = [
            Msg(
                "system",
                "You're a helpful assistant.",
                "system",
            ),
        ]
        self.msgs_conversation = [
            Msg(
                "user",
                [
                    TextBlock(
                        type="text",
                        text="What is the capital of France?",
                    ),
                    ImageBlock(
                        type="image",
                        source=URLSource(
                            type="url",
                            url=self.image_path,
                        ),
                    ),
                ],
                "user",
            ),
            Msg(
                "assistant",
                "The capital of France is Paris.",
                "assistant",
            ),
            Msg(
                "user",
                "What is the capital of Japan?",
                "user",
            ),
        ]

        self.msgs_tools = [
            Msg(
                "assistant",
                [
                    ToolUseBlock(
                        type="tool_use",
                        id="1",
                        name="get_capital",
                        input={"country": "Japan"},
                    ),
                ],
                "assistant",
            ),
            Msg(
                "system",
                [
                    ToolResultBlock(
                        type="tool_result",
                        id="1",
                        name="get_capital",
                        output=[
                            TextBlock(
                                type="text",
                                text="The capital of Japan is Tokyo.",
                            ),
                            ImageBlock(
                                type="image",
                                source=URLSource(
                                    type="url",
                                    url=self.image_path,
                                ),
                            ),
                        ],
                    ),
                ],
                "system",
            ),
            Msg(
                "assistant",
                "The capital of Japan is Tokyo.",
                "assistant",
            ),
        ]

        self.msgs_conversation_2 = [
            Msg(
                "user",
                "What is the capital of South Korea?",
                "user",
            ),
        ]

        self.msgs_tools_2 = [
            Msg(
                "assistant",
                [
                    ToolUseBlock(
                        type="tool_use",
                        id="2",
                        name="get_capital",
                        input={"country": "South Korea"},
                    ),
                ],
                "assistant",
            ),
            Msg(
                "system",
                [
                    ToolResultBlock(
                        type="tool_result",
                        id="1",
                        name="get_capital",
                        output=[
                            TextBlock(
                                type="text",
                                text="The capital of South Korea is Seoul.",
                            ),
                            ImageBlock(
                                type="image",
                                source=URLSource(
                                    type="url",
                                    url=self.image_path,
                                ),
                            ),
                        ],
                    ),
                ],
                "system",
            ),
            Msg(
                "assistant",
                "The capital of South Korea is Seoul.",
                "assistant",
            ),
        ]

        self.ground_truth_chat = [
            {
                "role": "system",
                "content": "You're a helpful assistant.",
            },
            {
                "role": "user",
                "content": "What is the capital of France?",
                "images": [
                    "ZmFrZSBpbWFnZSBjb250ZW50",
                ],
            },
            {
                "role": "assistant",
                "content": "The capital of France is Paris.",
            },
            {
                "role": "user",
                "content": "What is the capital of Japan?",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": {
                                "country": "Japan",
                            },
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of Japan is Tokyo.\n- The returned "
                "image can be found at: ./image.png",
                "name": "get_capital",
            },
            {
                "role": "assistant",
                "content": "The capital of Japan is Tokyo.",
            },
        ]

        self.ground_truth_multiagent = [
            {
                "role": "system",
                "content": "You're a helpful assistant.",
            },
            {
                "role": "user",
                "content": "# Conversation History\nThe content between"
                " <history></history> tags contains your"
                " conversation history\n<history>\nuser: What is"
                " the capital of France?\n\nassistant: The capital"
                " of France is Paris.\nuser: What is the capital"
                " of Japan?\n</history>",
                "images": [
                    "ZmFrZSBpbWFnZSBjb250ZW50",
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": {
                                "country": "Japan",
                            },
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of Japan is Tokyo.\n- The returned"
                " image can be found at: ./image.png",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": "<history>\nassistant: The"
                " capital of Japan is Tokyo.\n</history>",
            },
        ]

        self.ground_truth_multiagent_without_first_conversation = [
            {
                "role": "system",
                "content": "You're a helpful assistant.",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": {
                                "country": "Japan",
                            },
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of Japan is Tokyo.\n- The returned"
                " image can be found at: ./image.png",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": "# Conversation History\nThe content between"
                " <history></history> tags contains your"
                " conversation history\n<history>\nassistant: The"
                " capital of Japan is Tokyo.\n</history>",
            },
        ]

        self.ground_truth_multiagent_2 = [
            {
                "role": "system",
                "content": "You're a helpful assistant.",
            },
            {
                "role": "user",
                "content": "# Conversation History\nThe content between "
                "<history></history> tags contains your "
                "conversation history\n<history>\nuser: What is "
                "the capital of France?\n\nassistant: The capital "
                "of France is Paris.\nuser: What is the capital of "
                "Japan?\n</history>",
                "images": [
                    "ZmFrZSBpbWFnZSBjb250ZW50",
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": {
                                "country": "Japan",
                            },
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of Japan is Tokyo.\n- The returned "
                "image can be found at: ./image.png",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": "<history>\nassistant: The capital of Japan is "
                "Tokyo.\nuser: What is the capital of South Korea?"
                "\n</history>",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "2",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": {
                                "country": "South Korea",
                            },
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of South Korea is Seoul.\n- The "
                "returned image can be found at: ./image.png",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": "<history>\nassistant: The capital of South Korea"
                " is Seoul.\n</history>",
            },
        ]

    async def test_chat_formatter(self) -> None:
        """Test the Ollama chat formatter."""
        formatter = OllamaChatFormatter()

        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )

        self.assertListEqual(
            res,
            self.ground_truth_chat,
        )

        # Without system message
        res = await formatter.format(
            [*self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(
            res,
            self.ground_truth_chat[1:],
        )

        # Without conversation messages
        res = await formatter.format(
            [*self.msgs_system, *self.msgs_tools],
        )
        self.assertListEqual(
            res,
            self.ground_truth_chat[:1]
            + self.ground_truth_chat[-len(self.msgs_tools) :],
        )

        # Without tools messages
        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation],
        )
        self.assertListEqual(
            res,
            self.ground_truth_chat[: -len(self.msgs_tools)],
        )

    async def test_multi_agent_formatter(
        self,
    ) -> None:
        """Test the Ollama multi-agent formatter."""

        formatter = OllamaMultiAgentFormatter()

        # system + conversation + tools + conversation + tools
        res = await formatter.format(
            [
                *self.msgs_system,
                *self.msgs_conversation,
                *self.msgs_tools,
                *self.msgs_conversation_2,
                *self.msgs_tools_2,
            ],
        )

        self.assertListEqual(res, self.ground_truth_multiagent_2)

        # system + conversation + tools + conversation
        res = await formatter.format(
            [
                *self.msgs_system,
                *self.msgs_conversation,
                *self.msgs_tools,
                *self.msgs_conversation_2,
            ],
        )

        self.assertListEqual(
            res,
            self.ground_truth_multiagent_2[: -len(self.msgs_tools_2)],
        )

        # system + conversation + tools
        res = await formatter.format(
            [
                *self.msgs_system,
                *self.msgs_conversation,
                *self.msgs_tools,
            ],
        )

        self.assertListEqual(res, self.ground_truth_multiagent)

        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )

        self.assertListEqual(
            res,
            self.ground_truth_multiagent,
        )

        res = await formatter.format(
            [*self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(
            res,
            self.ground_truth_multiagent[1:],
        )

        res = await formatter.format(
            [*self.msgs_system, *self.msgs_tools],
        )
        self.assertListEqual(
            res,
            self.ground_truth_multiagent_without_first_conversation,
        )

        # Only system message
        res = await formatter.format(self.msgs_system)
        self.assertListEqual(res, self.ground_truth_multiagent[:1])

        # Only conversation messages
        res = await formatter.format(self.msgs_conversation)
        self.assertListEqual(
            res,
            self.ground_truth_multiagent[1 : -len(self.msgs_tools)],
        )

        # Only tools messages
        res = await formatter.format(self.msgs_tools)
        self.assertListEqual(
            res,
            self.ground_truth_multiagent_without_first_conversation[1:],
        )

    async def asyncTearDown(self) -> None:
        """Clean up the test environment."""
        if os.path.exists(self.image_path):
            os.remove(self.image_path)


if __name__ == "__main__":
    unittest.main()
