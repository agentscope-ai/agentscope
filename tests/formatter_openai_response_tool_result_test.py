# -*- coding: utf-8 -*-
"""Native tool-result tests for the OpenAI Responses formatter."""
import re
import tempfile
from functools import partial
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.formatter import OpenAIResponseFormatter
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    URLSource,
)


class TestOpenAIResponseToolResultFormatter(IsolatedAsyncioTestCase):
    """Tests for native multimodal function-call output."""

    def setUp(self) -> None:
        """Set up image fixtures."""
        image_source = URLSource(
            url="https://example.com/image.png",
            media_type="image/png",
        )
        self.image_url = str(image_source.url)
        self.image_b64 = "ZmFrZSBpbWFnZSBkYXRh"
        self.image_data_uri = f"data:image/png;base64,{self.image_b64}"

    async def test_native_multimodal_tool_result(self) -> None:
        """Multimodal tool results use the native ordered output array."""
        fmt = OpenAIResponseFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_img",
                        name="get_map",
                        input='{"city": "Tokyo"}',
                    ),
                    ToolResultBlock(
                        id="call_img",
                        name="get_map",
                        output=[
                            TextBlock(text="Here is the map."),
                            DataBlock(
                                source=Base64Source(
                                    data=self.image_b64,
                                    media_type="image/png",
                                ),
                            ),
                            TextBlock(text="The remote copy follows."),
                            DataBlock(
                                source=URLSource(
                                    url=self.image_url,
                                    media_type="image/png",
                                ),
                            ),
                            DataBlock(
                                source=URLSource(
                                    url="https://example.com/audio.mp3",
                                    media_type="audio/mpeg",
                                ),
                            ),
                            DataBlock(
                                name="report.pdf",
                                source=Base64Source(
                                    data="JVBERi0xLjQgZmFrZQ==",
                                    media_type="application/pdf",
                                ),
                            ),
                            TextBlock(text="End of result."),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="Here is the map of Tokyo."),
                ],
            ),
        ]

        res = await fmt.format(msgs)

        self.assertListEqual(
            [
                {
                    "type": "function_call",
                    "call_id": "call_img",
                    "name": "get_map",
                    "arguments": '{"city": "Tokyo"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_img",
                    "output": [
                        {"type": "input_text", "text": "Here is the map."},
                        {
                            "type": "input_image",
                            "image_url": self.image_data_uri,
                        },
                        {
                            "type": "input_text",
                            "text": "The remote copy follows.",
                        },
                        {
                            "type": "input_image",
                            "image_url": self.image_url,
                        },
                        {
                            "type": "input_text",
                            "text": (
                                "<system-reminder>A(n) audio file is "
                                "returned and can be accessed at the URL: "
                                "https://example.com/audio.mp3."
                                "</system-reminder>"
                            ),
                        },
                        {
                            "type": "input_file",
                            "filename": "report.pdf",
                            "file_data": (
                                "data:application/pdf;base64,"
                                "JVBERi0xLjQgZmFrZQ=="
                            ),
                        },
                        {"type": "input_text", "text": "End of result."},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Here is the map of Tokyo.",
                        },
                    ],
                },
            ],
            res,
        )

    async def test_unsupported_base64_audio_fallback(self) -> None:
        """Unsupported Base64 audio is persisted and returned as text."""
        fmt = OpenAIResponseFormatter()
        named_temp_file = tempfile.NamedTemporaryFile

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "agentscope.formatter._formatter_base.tempfile."
                "NamedTemporaryFile",
                side_effect=partial(named_temp_file, dir=temp_dir),
            ):
                res = await fmt.format(
                    [
                        AssistantMsg(
                            name="assistant",
                            content=[
                                ToolCallBlock(
                                    id="call_audio",
                                    name="get_audio",
                                    input="{}",
                                ),
                                ToolResultBlock(
                                    id="call_audio",
                                    name="get_audio",
                                    output=[
                                        TextBlock(text="audio-before"),
                                        DataBlock(
                                            source=Base64Source(
                                                data="SUQzBAAA",
                                                media_type="audio/mpeg",
                                            ),
                                        ),
                                        TextBlock(text="audio-after"),
                                    ],
                                    state=ToolResultState.SUCCESS,
                                ),
                            ],
                        ),
                    ],
                )

            output = res[1]["output"]
            self.assertIsInstance(output, str)
            path_match = re.search(
                r"saved locally at: (?P<path>.+)\.</system-reminder>",
                output,
            )
            self.assertIsNotNone(path_match)
            audio_path = Path(path_match.group("path"))
            with audio_path.open("rb") as audio_file:
                self.assertEqual(audio_file.read(), b"ID3\x04\x00\x00")
            self.assertEqual(
                output,
                "\n".join(
                    [
                        "audio-before",
                        "<system-reminder>A(n) audio file is returned and "
                        f"saved locally at: {audio_path}.</system-reminder>",
                        "audio-after",
                    ],
                ),
            )

        self.assertFalse(audio_path.exists())
