# -*- coding: utf-8 -*-
"""The Anthropic formatter unittests."""
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.formatter import (
    AnthropicMultiAgentFormatter,
    AnthropicChatFormatter,
)
from agentscope.message import (
    Msg,
    ToolUseBlock,
    ToolResultBlock,
    TextBlock,
    ImageBlock,
    URLSource,
)


class TestAnthropicChatFormatterFormatter(IsolatedAsyncioTestCase):
    """Unittest for the AnthropicChatFormatter."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.image_url = "www.example_image.png"

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
                            url=self.image_url,
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
                        output="The capital of Japan is Tokyo.",
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
                        output="The capital of South Korea is Seoul.",
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
                "content": [
                    {
                        "type": "text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of France?",
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": "www.example_image.png",
                        },
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of France is Paris.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of Japan?",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "id": "1",
                        "type": "tool_use",
                        "name": "get_capital",
                        "input": {
                            "country": "Japan",
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "1",
                        "content": [
                            {
                                "type": "text",
                                "text": "The capital of Japan is Tokyo.",
                            },
                        ],
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of Japan is Tokyo.",
                    },
                ],
            },
        ]

        self.ground_truth_multiagent = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "text": "# Conversation History\nThe content "
                        "between <history></history> tags contains "
                        "your conversation history\n<history>\nuser:"
                        " What is the capital of France?",
                        "type": "text",
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": "www.example_image.png",
                        },
                    },
                    {
                        "text": "assistant: The capital of France is Paris."
                        "\nuser: What is the capital of Japan?"
                        "\n</history>",
                        "type": "text",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "id": "1",
                        "type": "tool_use",
                        "name": "get_capital",
                        "input": {
                            "country": "Japan",
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "1",
                        "content": [
                            {
                                "type": "text",
                                "text": "The capital of Japan is Tokyo.",
                            },
                        ],
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "text": "<history>\nassistant:"
                        " The capital of Japan is Tokyo.\n</history>",
                        "type": "text",
                    },
                ],
            },
        ]

        self.ground_truth_multiagent_without_first_conversation = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "id": "1",
                        "type": "tool_use",
                        "name": "get_capital",
                        "input": {
                            "country": "Japan",
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "1",
                        "content": [
                            {
                                "type": "text",
                                "text": "The capital of Japan is Tokyo.",
                            },
                        ],
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "text": "# Conversation History\nThe content "
                        "between <history></history> tags contains "
                        "your conversation history\n<history>\nassistant:"
                        " The capital of Japan is Tokyo.\n</history>",
                        "type": "text",
                    },
                ],
            },
        ]

        self.ground_truth_multiagent_2 = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "text": "# Conversation History\nThe content between"
                        " <history></history> tags contains your"
                        " conversation history\n<history>\nuser: What"
                        " is the capital of France?",
                        "type": "text",
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": "www.example_image.png",
                        },
                    },
                    {
                        "text": "assistant: The capital of France is Paris."
                        "\nuser: What is the capital of Japan?"
                        "\n</history>",
                        "type": "text",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "id": "1",
                        "type": "tool_use",
                        "name": "get_capital",
                        "input": {
                            "country": "Japan",
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "1",
                        "content": [
                            {
                                "type": "text",
                                "text": "The capital of Japan is Tokyo.",
                            },
                        ],
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "text": "<history>\nassistant:"
                        " The capital of Japan is Tokyo.\nuser: What"
                        " is the capital of South Korea?\n</history>",
                        "type": "text",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "id": "2",
                        "type": "tool_use",
                        "name": "get_capital",
                        "input": {
                            "country": "South Korea",
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "1",
                        "content": [
                            {
                                "type": "text",
                                "text": "The capital of South Korea is Seoul.",
                            },
                        ],
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "text": "<history>\nassistant:"
                        " The capital of South Korea is Seoul."
                        "\n</history>",
                        "type": "text",
                    },
                ],
            },
        ]

    async def test_chat_formatter(self) -> None:
        """Test the chat formatter."""
        formatter = AnthropicChatFormatter()

        # Full history
        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )

        self.assertListEqual(self.ground_truth_chat, res)

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
            + self.ground_truth_chat[
                1
                + len(self.msgs_conversation) : 1
                + len(self.msgs_conversation)
                + len(self.msgs_tools)
            ],
        )

        # Without tool messages
        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation],
        )
        self.assertListEqual(
            res,
            self.ground_truth_chat[: 1 + len(self.msgs_conversation)],
        )

        # Empty messages
        res = await formatter.format([])
        self.assertListEqual(res, [])

    async def test_multiagent_formater(self) -> None:
        """Test the multi-agent formatter."""
        formatter = AnthropicMultiAgentFormatter()

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

        # Without system message
        res = await formatter.format(
            [*self.msgs_conversation, *self.msgs_tools],
        )

        self.assertListEqual(
            res,
            self.ground_truth_multiagent[
                1 : 1 + len(self.msgs_conversation) + len(self.msgs_tools) - 2
            ],
        )

        # Without conversation messages
        res = await formatter.format(
            [*self.msgs_system, *self.msgs_tools],
        )
        self.assertListEqual(
            res,
            self.ground_truth_multiagent_without_first_conversation,
        )

        # Without tool messages
        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation],
        )
        self.assertListEqual(
            res,
            self.ground_truth_multiagent[:2],
        )

        # With only system message
        res = await formatter.format(self.msgs_system)
        self.assertListEqual(
            res,
            self.ground_truth_multiagent[:1],
        )

        # With only conversation messages
        res = await formatter.format(self.msgs_conversation)
        self.assertListEqual(
            res,
            self.ground_truth_multiagent[1 : -len(self.msgs_tools)],
        )

        # With only tool messages
        res = await formatter.format(self.msgs_tools)
        self.assertListEqual(
            res,
            self.ground_truth_multiagent_without_first_conversation[1:],
        )
