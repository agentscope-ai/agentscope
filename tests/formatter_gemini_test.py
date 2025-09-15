# -*- coding: utf-8 -*-
"""The gemini formatter unittests."""
import os
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from agentscope.formatter import GeminiChatFormatter, GeminiMultiAgentFormatter
from agentscope.message import (
    Msg,
    URLSource,
    TextBlock,
    AudioBlock,
    ImageBlock,
    ToolUseBlock,
    ToolResultBlock,
    Base64Source,
)


class TestGeminiFormatter(IsolatedAsyncioTestCase):
    """Unittest for the Gemini formatter."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.image_path = "./image.png"
        with open(self.image_path, "wb") as f:
            f.write(b"fake image content")

        self.mock_audio_path = (
            "/var/folders/gf/krg8x_ws409cpw_46b2s6rjc0000gn/T/tmpfymnv2w9.wav"
        )

        self.audio_path = "./audio.mp3"
        with open(self.audio_path, "wb") as f:
            f.write(b"fake audio content")

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
                            url=self.audio_path,
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
                "role": "user",
                "parts": [
                    {
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "What is the capital of France?",
                    },
                    {
                        "inline_data": {
                            "data": "ZmFrZSBpbWFnZSBjb250ZW50",
                            "mime_type": "image/png",
                        },
                    },
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "text": "The capital of France is Paris.",
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "What is the capital of Germany?",
                    },
                    {
                        "inline_data": {
                            "data": "ZmFrZSBhdWRpbyBjb250ZW50",
                            "mime_type": "audio/mp3",
                        },
                    },
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "text": "The capital of Germany is Berlin.",
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "What is the capital of Japan?",
                    },
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": "1",
                            "name": "get_capital",
                            "args": {
                                "country": "Japan",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "function_response": {
                            "id": "1",
                            "name": "get_capital",
                            "response": {
                                "output": "- The capital of Japan is Tokyo.\n"
                                "- The returned image can be found"
                                " at: ./image.png\n- The returned "
                                "audio can be found at: "
                                f"{self.mock_audio_path}",
                            },
                        },
                    },
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "text": "The capital of Japan is Tokyo.",
                    },
                ],
            },
        ]
        self.ground_truth_multiagent = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "# Conversation History\nThe content between"
                        " <history></history> tags contains your"
                        " conversation history\n<history>user: What"
                        " is the capital of France?",
                    },
                    {
                        "inline_data": {
                            "data": "ZmFrZSBpbWFnZSBjb250ZW50",
                            "mime_type": "image/png",
                        },
                    },
                    {
                        "text": "assistant: The capital of France is Paris."
                        "\nuser: What is the capital of Germany?",
                    },
                    {
                        "inline_data": {
                            "data": "ZmFrZSBhdWRpbyBjb250ZW50",
                            "mime_type": "audio/mp3",
                        },
                    },
                    {
                        "text": "assistant: The capital of Germany is Berlin."
                        "\nuser: What is the capital of Japan?"
                        "\n</history>",
                    },
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": "1",
                            "name": "get_capital",
                            "args": {
                                "country": "Japan",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "function_response": {
                            "id": "1",
                            "name": "get_capital",
                            "response": {
                                "output": "- The capital of Japan is Tokyo."
                                "\n- The returned image can be found"
                                " at: ./image.png\n- The returned"
                                " audio can be found at: "
                                f"{self.mock_audio_path}",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "<history>assistant:"
                        " The capital of Japan is Tokyo.\n</history>",
                    },
                ],
            },
        ]

        self.ground_truth_multiagent_without_first_conversation = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": "1",
                            "name": "get_capital",
                            "args": {
                                "country": "Japan",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "function_response": {
                            "id": "1",
                            "name": "get_capital",
                            "response": {
                                "output": "- The capital of Japan is Tokyo."
                                "\n- The returned image can be found"
                                " at: ./image.png\n- The returned"
                                " audio can be found at: "
                                f"{self.mock_audio_path}",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "# Conversation History\nThe content between"
                        " <history></history> tags contains your"
                        " conversation history\n<history>assistant:"
                        " The capital of Japan is Tokyo.\n</history>",
                    },
                ],
            },
        ]

        self.ground_truth_multiagent_2 = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "# Conversation History\nThe content between "
                        "<history></history> tags contains your "
                        "conversation history\n<history>user: What is "
                        "the capital of France?",
                    },
                    {
                        "inline_data": {
                            "data": "ZmFrZSBpbWFnZSBjb250ZW50",
                            "mime_type": "image/png",
                        },
                    },
                    {
                        "text": "assistant: The capital of France is Paris."
                        "\nuser: What is the capital of Germany?",
                    },
                    {
                        "inline_data": {
                            "data": "ZmFrZSBhdWRpbyBjb250ZW50",
                            "mime_type": "audio/mp3",
                        },
                    },
                    {
                        "text": "assistant: The capital of Germany is Berlin."
                        "\nuser: What is the capital of Japan?\n</history>",
                    },
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": "1",
                            "name": "get_capital",
                            "args": {
                                "country": "Japan",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "function_response": {
                            "id": "1",
                            "name": "get_capital",
                            "response": {
                                "output": "- The capital of Japan is Tokyo."
                                "\n- The returned image can be found "
                                "at: ./image.png\n- The returned audio"
                                " can be found at: "
                                f"{self.mock_audio_path}",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "<history>assistant: "
                        "The capital of Japan is Tokyo.\nuser: What "
                        "is the capital of South Korea?\n</history>",
                    },
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": "1",
                            "name": "get_capital",
                            "args": {
                                "country": "South Korea",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "function_response": {
                            "id": "1",
                            "name": "get_capital",
                            "response": {
                                "output": "- The capital of South Korea is "
                                "Seoul.\n- The returned image can "
                                "be found at: ./image.png\n- The "
                                "returned audio can be found at: "
                                f"{self.mock_audio_path}",
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": "<history>assistant: "
                        "The capital of South Korea is Seoul."
                        "\n</history>",
                    },
                ],
            },
        ]

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_chat_formatter(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Test the gemini chat formatter."""
        mock_save_base64_data.return_value = self.mock_audio_path
        formatter = GeminiChatFormatter()

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

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_multi_agent_formatter(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Test the gemini multi-agent formatter."""
        mock_save_base64_data.return_value = self.mock_audio_path

        formatter = GeminiMultiAgentFormatter()

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
        if os.path.exists(self.audio_path):
            os.remove(self.audio_path)
