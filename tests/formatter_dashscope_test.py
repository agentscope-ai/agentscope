# -*- coding: utf-8 -*-
"""The dashscope formatter unittests."""
import os
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from agentscope.formatter import (
    DashScopeMultiAgentFormatter,
    DashScopeChatFormatter,
)
from agentscope.message import (
    Msg,
    ToolUseBlock,
    ToolResultBlock,
    TextBlock,
    ImageBlock,
    AudioBlock,
    URLSource,
    Base64Source,
)


class TestDashScopeFormatter(IsolatedAsyncioTestCase):
    """Unittest for the DashScopeFormatter."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.image_path = "./image.png"
        with open(self.image_path, "wb") as f:
            f.write(b"fake image content")

        self.mock_audio_path = (
            "/var/folders/gf/krg8x_ws409cpw_46b2s6rjc0000gn/T/tmpfymnv2w9.wav"
        )

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
                [
                    TextBlock(
                        type="text",
                        text="What is the capital of Germany?",
                    ),
                    AudioBlock(
                        type="audio",
                        source=URLSource(
                            type="url",
                            url="https://example.com/audio1.mp3",
                        ),
                    ),
                ],
                "user",
            ),
            Msg(
                "assistant",
                "The capital of Germany is Berlin.",
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
                            AudioBlock(
                                type="audio",
                                source=Base64Source(
                                    type="base64",
                                    media_type="audio/wav",
                                    data="ZmFrZSBhdWRpbyBjb250ZW50",
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
                        id="1",
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
                        id="2",
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
                            AudioBlock(
                                type="audio",
                                source=Base64Source(
                                    type="base64",
                                    media_type="audio/wav",
                                    data="ZmFrZSBhdWRpbyBjb250ZW50",
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
                "content": [
                    {
                        "text": "What is the capital of France?",
                    },
                    {
                        "image": f"file://{os.path.abspath(self.image_path)}",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "The capital of France is Paris.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "text": "What is the capital of Germany?",
                    },
                    {
                        "audio": "https://example.com/audio1.mp3",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "The capital of Germany is Berlin.",
            },
            {
                "role": "user",
                "content": "What is the capital of Japan?",
            },
            {
                "role": "assistant",
                "content": [{"text": None}],
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": '{"country": "Japan"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of Japan is Tokyo.\n"
                "- The returned image can be found at: ./image.png"
                "\n- The returned audio can be found at: "
                f"{self.mock_audio_path}",
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
                "content": [
                    {
                        "text": "# Conversation History\nThe content between"
                        " <history></history> tags contains your"
                        " conversation history\n<history>\nuser: What"
                        " is the capital of France?",
                    },
                    {
                        "image": f"file://{os.path.abspath(self.image_path)}",
                    },
                    {
                        "text": "assistant: The capital of France is Paris."
                        "\nuser: What is the capital of Germany?",
                    },
                    {
                        "audio": "https://example.com/audio1.mp3",
                    },
                    {
                        "text": "assistant: The capital of Germany is Berlin."
                        "\nuser: What is the capital of Japan?"
                        "\n</history>",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "text": None,
                    },
                ],
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": '{"country": "Japan"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of Japan is Tokyo.\n- The returned"
                " image can be found at: ./image.png\n- The"
                " returned audio can be found at: "
                f"{self.mock_audio_path}",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": "<history>\nassistant:"
                " The capital of Japan is Tokyo.\n</history>",
            },
        ]

        self.ground_truth_multiagent_without_first_conversation = [
            {
                "role": "system",
                "content": "You're a helpful assistant.",
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "text": None,
                    },
                ],
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": '{"country": "Japan"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of Japan is Tokyo.\n- The returned"
                " image can be found at: ./image.png\n- The"
                " returned audio can be found at: "
                f"{self.mock_audio_path}",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": "# Conversation History\nThe content between"
                " <history></history> tags contains your"
                " conversation history\n<history>\nassistant:"
                " The capital of Japan is Tokyo.\n</history>",
            },
        ]

        self.ground_truth_multiagent_2 = [
            {
                "role": "system",
                "content": "You're a helpful assistant.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "text": "# Conversation History\nThe content between "
                        "<history></history> tags contains your conversation "
                        "history\n<history>\nuser: What is the capital of "
                        "France?",
                    },
                    {
                        "image": f"file://{os.path.abspath(self.image_path)}",
                    },
                    {
                        "text": "assistant: The capital of France is Paris."
                        "\nuser: What is the capital of Germany?",
                    },
                    {
                        "audio": "https://example.com/audio1.mp3",
                    },
                    {
                        "text": "assistant: The capital of Germany is Berlin."
                        "\nuser: What is the capital of Japan?"
                        "\n</history>",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "text": None,
                    },
                ],
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": '{"country": "Japan"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": "- The capital of Japan is Tokyo.\n- The returned"
                " image can be found at: ./image.png\n- The returned audio can"
                f" be found at: {self.mock_audio_path}",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": "<history>\nassistant: The capital of Japan "
                "is Tokyo.\nuser: What is the capital of South Korea?"
                "\n</history>",
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "text": None,
                    },
                ],
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": '{"country": "South Korea"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "2",
                "content": "- The capital of South Korea is Seoul.\n- The "
                "returned image can be found at: ./image.png\n- The returned"
                " audio can be found at: "
                f"{self.mock_audio_path}",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": "<history>\nassistant: The capital of South"
                " Korea is Seoul.\n</history>",
            },
        ]

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_chat_formatter(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Test the chat formatter."""
        mock_save_base64_data.return_value = self.mock_audio_path

        formatter = DashScopeChatFormatter()

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
            + self.ground_truth_chat[-len(self.msgs_tools) :],
        )

        # Without tool messages
        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation],
        )
        self.assertListEqual(
            res,
            self.ground_truth_chat[: -len(self.msgs_tools)],
        )

        # Empty messages
        res = await formatter.format([])
        self.assertListEqual(res, [])

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_multiagent_formater(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Test the multi-agent formatter."""
        mock_save_base64_data.return_value = self.mock_audio_path

        formatter = DashScopeMultiAgentFormatter()

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
        self.assertListEqual(res, self.ground_truth_multiagent)

        # Without system message
        res = await formatter.format(
            [*self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(
            res,
            self.ground_truth_multiagent[1:],
        )

        # Without conversation messages
        res = await formatter.format(
            [*self.msgs_system, *self.msgs_tools],
        )
        self.assertListEqual(
            res,
            self.ground_truth_multiagent_without_first_conversation,
        )

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

        # Without only tool messages
        res = await formatter.format(self.msgs_tools)
        self.assertListEqual(
            res,
            self.ground_truth_multiagent_without_first_conversation[1:],
        )

    async def asyncTearDown(self) -> None:
        """Clean up the test environment."""
        if os.path.exists(self.image_path):
            os.remove(self.image_path)
