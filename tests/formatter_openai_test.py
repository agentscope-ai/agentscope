# -*- coding: utf-8 -*-
"""The OpenAI formatter unittests."""
import os
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from agentscope.formatter import OpenAIChatFormatter
from agentscope.formatter._openai_formatter import OpenAIMultiAgentFormatter
from agentscope.message import (
    Msg,
    TextBlock,
    ImageBlock,
    AudioBlock,
    URLSource,
    ToolResultBlock,
    ToolUseBlock,
    Base64Source,
)


class TestOpenAIFormatter(IsolatedAsyncioTestCase):
    """OpenAI formatter unittests."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.image_path = os.path.abspath("./image.png")
        with open(self.image_path, "wb") as f:
            f.write(b"fake image content")

        self.mock_audio_path = (
            "/var/folders/gf/krg8x_ws409cpw_46b2s6rjc0000gn/T/tmpfymnv2w9.wav"
        )

        self.audio_path = os.path.abspath("./audio.wav")
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

        self.ground_truth_chat = [
            {
                "role": "system",
                "name": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of France?",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;"
                            "base64,ZmFrZSBpbWFnZSBjb250ZW50",
                        },
                    },
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of France is Paris.",
                    },
                ],
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of Germany?",
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": "ZmFrZSBhdWRpbyBjb250ZW50",
                            "format": "wav",
                        },
                    },
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of Germany is Berlin.",
                    },
                ],
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of Japan?",
                    },
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": None,
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
                "- The returned image can be found at: "
                f"{self.image_path}\n"
                "- The returned audio can be found at: "
                f"{self.mock_audio_path}",
                "name": "get_capital",
            },
            {
                "role": "assistant",
                "name": "assistant",
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
                        "type": "text",
                        "text": "# Conversation History\n"
                        "The content between <history></history> tags contains"
                        " your conversation history\n"
                        "<history>\n"
                        "user: What is the capital of France?\n"
                        "assistant: The capital of France is Paris.\n"
                        "user: What is the capital of Germany?\n"
                        "assistant: The capital of Germany is Berlin.\n"
                        "user: What is the capital of Japan?\n"
                        "</history>",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,"
                            "ZmFrZSBpbWFnZSBjb250ZW50",
                        },
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": "ZmFrZSBhdWRpbyBjb250ZW50",
                            "format": "wav",
                        },
                    },
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": None,
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
                "- The returned image can be found at: "
                f"{self.image_path}\n"
                "- The returned audio can be found at: "
                f"{self.mock_audio_path}",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "<history>\n"
                        "assistant: The capital of Japan is Tokyo.\n"
                        "</history>",
                    },
                ],
            },
        ]

        self.ground_truth_multiagent_without_conversation = [
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
                "name": "assistant",
                "content": None,
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
                "- The returned image can be found at: "
                f"{self.image_path}\n"
                "- The returned audio can be found at: "
                f"{self.mock_audio_path}",
                "name": "get_capital",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "# Conversation History\n"
                        "The content between <history></history> tags contains"
                        " your conversation history\n<history>\n"
                        "assistant: The capital of Japan is Tokyo.\n"
                        "</history>",
                    },
                ],
            },
        ]

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_formatter(self, mock_save_base64_data: MagicMock) -> None:
        """Test the chat formatter."""
        mock_save_base64_data.return_value = self.mock_audio_path

        formatter = OpenAIChatFormatter()

        # Full history
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
    async def test_multiagent_formatter(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Test the OpenAI multi-agent formatter."""
        mock_save_base64_data.return_value = self.mock_audio_path

        formatter = OpenAIMultiAgentFormatter()

        # Full test: system + conversation + tools
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
            self.ground_truth_multiagent_without_conversation,
        )

        # Only system message
        res = await formatter.format(self.msgs_system)
        self.assertListEqual(res, self.ground_truth_multiagent[:1])

        # Only tools messages
        res = await formatter.format(self.msgs_tools)
        self.assertListEqual(
            res,
            self.ground_truth_multiagent_without_conversation[1:],
        )

    async def asyncTearDown(self) -> None:
        """Clean up the test environment."""
        if os.path.exists(self.image_path):
            os.remove(self.image_path)
        if os.path.exists(self.audio_path):
            os.remove(self.audio_path)
