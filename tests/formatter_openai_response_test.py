# -*- coding: utf-8 -*-
"""The OpenAI Response formatter unittests."""
import os
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from agentscope.formatter._openai_resoponse_formatter import (
    OpenAIResponseChatFormatter,
    OpenAIResponseMultiAgentFormatter,
)
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


class TestOpenAIResponseFormatter(IsolatedAsyncioTestCase):
    """OpenAI Response formatter unittests."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.image_path = os.path.abspath("./image_resp.png")
        with open(self.image_path, "wb") as f:
            f.write(b"fake image content")

        self.mock_audio_path = (
            "/var/folders/gf/krg8x_ws409cpw_46b2s6rjc0000gn/T/tmpfymnv2w9.wav"
        )

        self.audio_path = os.path.abspath("./audio_resp.wav")
        with open(self.audio_path, "wb") as f:
            f.write(b"fake audio content")

        self.msgs_system = [
            Msg("system", "You're a helpful assistant.", "system"),
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
                        source=URLSource(type="url", url=self.image_path),
                    ),
                ],
                "user",
            ),
            Msg("assistant", "The capital of France is Paris.", "assistant"),
            Msg(
                "user",
                [
                    TextBlock(
                        type="text",
                        text="What is the capital of Germany?",
                    ),
                    AudioBlock(
                        type="audio",
                        source=URLSource(type="url", url=self.audio_path),
                    ),
                ],
                "user",
            ),
            Msg(
                "assistant",
                "The capital of Germany is Berlin.",
                "assistant",
            ),
            Msg("user", "What is the capital of Japan?", "user"),
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
            Msg("assistant", "The capital of Japan is Tokyo.", "assistant"),
        ]

        self.ground_truth_chat = [
            {
                "role": "system",
                "name": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "What is the capital of France?",
                    },
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;"
                        "base64,ZmFrZSBpbWFnZSBjb250ZW50",
                    },
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": [
                    {
                        "type": "input_text",
                        "text": "The capital of France is Paris.",
                    },
                ],
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {
                        "type": "input_text",
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
                        "type": "input_text",
                        "text": "The capital of Germany is Berlin.",
                    },
                ],
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "What is the capital of Japan?",
                    },
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": [],
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
                "role": "assistant",
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
                        "type": "input_text",
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
                        "type": "input_text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
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
                        "type": "input_image",
                        "image_url": "data:image/png;base64,"
                        "ZmFrZSBpbWFnZSBjb250ZW50",
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
                "content": [],
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
                "role": "assistant",
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
                        "type": "input_text",
                        "text": "<history>\n"
                        "assistant: The capital of Japan is Tokyo.\n"
                        "</history>",
                    },
                ],
            },
        ]

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_chat_formatter(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Test the OpenAI Response chat formatter with full history."""
        mock_save_base64_data.return_value = self.mock_audio_path
        formatter = OpenAIResponseChatFormatter()

        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(res, self.ground_truth_chat)

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_chat_formatter_with_promote_images(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Test chat formatter with promote_tool_result_images=True."""
        mock_save_base64_data.return_value = self.mock_audio_path
        formatter = OpenAIResponseChatFormatter(
            promote_tool_result_images=True,
        )

        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )

        # Verify tool result format
        tool_results = [m for m in res if m.get("tool_call_id") is not None]
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0]["tool_call_id"], "1")

        # Verify promoted image block is inserted as a separate user message
        promoted_msgs = [
            m
            for m in res
            if m.get("role") == "user"
            and any(
                "<system-info>" in (b.get("text", "") or "")
                for b in (m.get("content") or [])
            )
        ]
        self.assertEqual(len(promoted_msgs), 1)

        promoted_content = promoted_msgs[0]["content"]
        img_blocks = [
            b for b in promoted_content if b.get("type") == "input_image"
        ]
        self.assertEqual(len(img_blocks), 1)

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_multiagent_formatter(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Test the OpenAI Response multi-agent formatter."""
        mock_save_base64_data.return_value = self.mock_audio_path
        formatter = OpenAIResponseMultiAgentFormatter()

        res = await formatter.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(res, self.ground_truth_multiagent)

    @patch("agentscope.formatter._formatter_base._save_base64_data")
    async def test_multiagent_system_message_uses_input_text(
        self,
        mock_save_base64_data: MagicMock,
    ) -> None:
        """Verify multi-agent formatter system message uses 'input_text'."""
        mock_save_base64_data.return_value = self.mock_audio_path
        formatter = OpenAIResponseMultiAgentFormatter()

        res = await formatter.format(self.msgs_system)
        system_msg = res[0]
        self.assertEqual(system_msg["role"], "system")
        for block in system_msg["content"]:
            self.assertEqual(block["type"], "input_text")

    async def asyncTearDown(self) -> None:
        """Clean up the test environment."""
        if os.path.exists(self.image_path):
            os.remove(self.image_path)
        if os.path.exists(self.audio_path):
            os.remove(self.audio_path)
