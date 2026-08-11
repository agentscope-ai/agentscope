# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for OpenAIChatFormatter and
OpenAIMultiAgentFormatter, following the reference test style with exact
ground-truth comparisons.
"""
import base64
import os
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import agentscope.formatter._formatter_base as formatter_base
from agentscope.formatter import (
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)
from agentscope.formatter._formatter_base import (
    FormatterBase,
    _cleanup_unsupported_media_temp_files,
)
from agentscope.message import (
    UserMsg,
    AssistantMsg,
    SystemMsg,
    TextBlock,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    Base64Source,
    URLSource,
    ThinkingBlock,
    HintBlock,
)


_FIXED_ID = "TESTID1234567"


class TestOpenAIFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for OpenAI Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared message fixtures and expected ground-truth dicts."""
        _img_src = URLSource(
            url="https://example.com/image.png",
            media_type="image/png",
        )
        self.image_url = str(_img_src.url)

        self.image_b64 = "ZmFrZSBpbWFnZSBkYXRh"
        self.image_data_uri = f"data:image/png;base64,{self.image_b64}"

        # ---------------------------------------------------------------
        # Message fixtures
        # (No audio in conversation: OpenAI URL audio requires a download)
        # ---------------------------------------------------------------
        self.msgs_system = [
            SystemMsg(
                name="system",
                content="You're a helpful assistant.",
            ),
        ]

        self.msgs_conversation = [
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="What is the capital of France?"),
                    DataBlock(
                        source=URLSource(
                            url=self.image_url,
                            media_type="image/png",
                        ),
                    ),
                ],
            ),
            AssistantMsg(
                name="assistant",
                content="The capital of France is Paris.",
            ),
            UserMsg(
                name="user",
                content="What is the capital of Germany?",
            ),
            AssistantMsg(
                name="assistant",
                content="The capital of Germany is Berlin.",
            ),
            UserMsg(
                name="user",
                content="What is the capital of Japan?",
            ),
        ]

        self.msgs_tools = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_1",
                        name="get_capital",
                        input='{"country": "Japan"}',
                    ),
                    ToolResultBlock(
                        id="call_1",
                        name="get_capital",
                        output=[
                            TextBlock(text="The capital of Japan is Tokyo."),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="The capital of Japan is Tokyo."),
                ],
            ),
        ]

        # ---------------------------------------------------------------
        # Ground truth: OpenAIChatFormatter
        #   - Content is a list of {"type": ..., ...} dicts.
        #   - Tool-result content is a plain string.
        #   - Messages have a "name" field.
        # ---------------------------------------------------------------
        self.gt_chat = [
            {
                "role": "system",
                "name": "system",
                "content": [
                    {"type": "text", "text": "You're a helpful assistant."},
                ],
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {"type": "text", "text": "What is the capital of France?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": self.image_url},
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
                    {"type": "text", "text": "What is the capital of Japan?"},
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
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
                "tool_call_id": "call_1",
                "content": "The capital of Japan is Tokyo.",
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

        # ---------------------------------------------------------------
        # Ground truth: OpenAIMultiAgentFormatter
        #   - System content is a plain string.
        #   - All conversation text is collapsed into a single text block,
        #     with media blocks appended after.
        #   - No "name" field on the user wrapper message.
        # ---------------------------------------------------------------
        _hist_prompt = OpenAIMultiAgentFormatter().conversation_history_prompt

        _conv_text = (
            "user: What is the capital of France?\n"
            "assistant: The capital of France is Paris.\n"
            "user: What is the capital of Germany?\n"
            "assistant: The capital of Germany is Berlin.\n"
            "user: What is the capital of Japan?"
        )

        self._gt_trailing_asst = {
            "role": "assistant",
            "name": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "The capital of Japan is Tokyo.",
                },
            ],
        }

        self._gt_tool_call = {
            "role": "assistant",
            "name": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_capital",
                        "arguments": '{"country": "Japan"}',
                    },
                },
            ],
        }
        self._gt_tool_result = {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "The capital of Japan is Tokyo.",
            "name": "get_capital",
        }

        self.gt_multiagent = [
            {
                "role": "system",
                "content": "You're a helpful assistant.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            _hist_prompt
                            + "<history>\n"
                            + _conv_text
                            + "\n</history>"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": self.image_url},
                    },
                ],
            },
            self._gt_tool_call,
            self._gt_tool_result,
            self._gt_trailing_asst,
        ]

    # -------------------------------------------------------------------
    # OpenAIChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = OpenAIChatFormatter()

        # Full history
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(self.gt_chat, res)

        # Without system
        res = await fmt.format([*self.msgs_conversation, *self.msgs_tools])
        self.assertListEqual(self.gt_chat[1:], res)

        # Without conversation
        n_tools_gt = len(self.gt_chat) - 1 - len(self.msgs_conversation)
        res = await fmt.format([*self.msgs_system, *self.msgs_tools])
        self.assertListEqual(
            [self.gt_chat[0]] + self.gt_chat[-n_tools_gt:],
            res,
        )

        # Without tools
        res = await fmt.format([*self.msgs_system, *self.msgs_conversation])
        self.assertListEqual(self.gt_chat[:-n_tools_gt], res)

        # Empty
        res = await fmt.format([])
        self.assertListEqual([], res)

    async def test_chat_formatter_base64_image(self) -> None:
        """Base64-encoded image is inlined as a data URI."""
        fmt = OpenAIChatFormatter()
        msgs = [
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="What's in this image?"),
                    DataBlock(
                        source=Base64Source(
                            data=self.image_b64,
                            media_type="image/png",
                        ),
                    ),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "user",
                    "name": "user",
                    "content": [
                        {"type": "text", "text": "What's in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": self.image_data_uri},
                        },
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_thinking_dropped(self) -> None:
        """ThinkingBlock is silently dropped by OpenAI formatter."""
        fmt = OpenAIChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="inner thoughts"),
                    TextBlock(text="reply"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [{"type": "text", "text": "reply"}],
                },
            ],
            res,
        )

    @patch(
        "agentscope.formatter._formatter_base.shortuuid.uuid",
        return_value=_FIXED_ID,
    )
    async def test_chat_formatter_url_image_in_tool_result(
        self,
        _mock_uuid: object,
    ) -> None:
        """URL images in tool results are promoted to a follow-up user
        message."""
        fmt = OpenAIChatFormatter()
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
                                source=URLSource(
                                    url=self.image_url,
                                    media_type="image/png",
                                ),
                            ),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="Here is the map of Tokyo."),
                ],
            ),
        ]
        res = await fmt.format(msgs)

        expected_tool_content = (
            "Here is the map.\n"
            f"<system-reminder>A(n) image file is returned "
            f"and will be presented to you with the identifier "
            f"[{_FIXED_ID}].</system-reminder>"
        )
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_img",
                            "type": "function",
                            "function": {
                                "name": "get_map",
                                "arguments": '{"city": "Tokyo"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_img",
                    "content": expected_tool_content,
                    "name": "get_map",
                },
                {
                    "role": "user",
                    "name": "system-reminder",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<system-reminder>The multimodal data "
                                "and their identifiers are listed as "
                                "follows:"
                            ),
                        },
                        {
                            "type": "text",
                            "text": f"- {_FIXED_ID} (image file): ",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": self.image_url},
                        },
                        {
                            "type": "text",
                            "text": "</system-reminder>",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Here is the map of Tokyo.",
                        },
                    ],
                },
            ],
            res,
        )

    # -------------------------------------------------------------------
    # OpenAIMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = OpenAIMultiAgentFormatter()

        # Full history
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(self.gt_multiagent, res)

        # Without system
        res = await fmt.format([*self.msgs_conversation, *self.msgs_tools])
        self.assertListEqual(self.gt_multiagent[1:], res)

        # Without tools
        res = await fmt.format([*self.msgs_system, *self.msgs_conversation])
        self.assertListEqual(self.gt_multiagent[:2], res)

        # System only
        res = await fmt.format(self.msgs_system)
        self.assertListEqual([self.gt_multiagent[0]], res)

        # Conversation only
        res = await fmt.format(self.msgs_conversation)
        self.assertListEqual([self.gt_multiagent[1]], res)

        # Tools only
        res = await fmt.format(self.msgs_tools)
        self.assertListEqual(
            [
                self._gt_tool_call,
                self._gt_tool_result,
                self._gt_trailing_asst,
            ],
            res,
        )

        # System + tools (no conversation)
        res = await fmt.format([*self.msgs_system, *self.msgs_tools])
        self.assertListEqual(
            [
                self.gt_multiagent[0],
                self._gt_tool_call,
                self._gt_tool_result,
                self._gt_trailing_asst,
            ],
            res,
        )

        # Empty
        res = await fmt.format([])
        self.assertListEqual([], res)

    async def test_chat_formatter_complex_multi_step(self) -> None:
        """Complex multi-step sequence with interleaved thinking, text,
        tool calls, and tool results."""
        fmt = OpenAIChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="thinking_1"),
                    TextBlock(text="text_1"),
                    ToolCallBlock(
                        id="call_1",
                        name="func_1",
                        input='{"arg": "value1"}',
                    ),
                    ToolCallBlock(
                        id="call_2",
                        name="func_2",
                        input='{"arg": "value2"}',
                    ),
                    ToolResultBlock(
                        id="call_1",
                        name="func_1",
                        output=[TextBlock(text="result_1")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ToolResultBlock(
                        id="call_2",
                        name="func_2",
                        output=[TextBlock(text="result_2")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ThinkingBlock(thinking="thinking_2"),
                    TextBlock(text="text_2"),
                    ToolCallBlock(
                        id="call_3",
                        name="func_3",
                        input='{"arg": "value3"}',
                    ),
                    ToolResultBlock(
                        id="call_3",
                        name="func_3",
                        output=[TextBlock(text="result_3")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ToolCallBlock(
                        id="call_4",
                        name="func_4",
                        input='{"arg": "value4"}',
                    ),
                    ToolResultBlock(
                        id="call_4",
                        name="func_4",
                        output=[TextBlock(text="result_4")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ThinkingBlock(thinking="thinking_3"),
                    TextBlock(text="text_3"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [{"type": "text", "text": "text_1"}],
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "func_1",
                                "arguments": '{"arg": "value1"}',
                            },
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "func_2",
                                "arguments": '{"arg": "value2"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "result_1",
                    "name": "func_1",
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_2",
                    "content": "result_2",
                    "name": "func_2",
                },
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [{"type": "text", "text": "text_2"}],
                    "tool_calls": [
                        {
                            "id": "call_3",
                            "type": "function",
                            "function": {
                                "name": "func_3",
                                "arguments": '{"arg": "value3"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_3",
                    "content": "result_3",
                    "name": "func_3",
                },
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_4",
                            "type": "function",
                            "function": {
                                "name": "func_4",
                                "arguments": '{"arg": "value4"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_4",
                    "content": "result_4",
                    "name": "func_4",
                },
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [{"type": "text", "text": "text_3"}],
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = OpenAIChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    TextBlock(text="Let me think about that."),
                    HintBlock(hint="Remember to be concise."),
                    TextBlock(text="Here is my answer."),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me think about that."},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Remember to be concise."},
                    ],
                },
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [
                        {"type": "text", "text": "Here is my answer."},
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block_multimodal(self) -> None:
        """Multimodal HintBlock becomes a single user message with text +
        image."""
        fmt = OpenAIChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    HintBlock(
                        hint=[
                            TextBlock(text="Inspect this screenshot:"),
                            DataBlock(
                                source=Base64Source(
                                    data=self.image_b64,
                                    media_type="image/png",
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Inspect this screenshot:",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": self.image_data_uri},
                        },
                    ],
                },
            ],
            res,
        )


class _TextOnlyFormatter(FormatterBase):
    """A formatter that accepts only ``text/plain``; everything else goes
    through ``convert_tool_result_to_string``'s unsupported-media path."""

    async def format(
        self,
        *args: object,
        **kwargs: object,
    ) -> list[dict]:  # pragma: no cover
        return []


class FormatterBaseUnsupportedMediaTest(IsolatedAsyncioTestCase):
    """Unsupported-media Base64Source storage regressions (issue #2173)."""

    def setUp(self) -> None:
        """Seed a text-only formatter and two base64 fixtures."""
        _cleanup_unsupported_media_temp_files()
        self._fmt = _TextOnlyFormatter(input_types=["text/plain"])
        self._png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakehdr"
        self._png_b64 = base64.b64encode(self._png_bytes).decode("ascii")
        self._mp3_bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00fake mp3 payload"
        self._mp3_b64 = base64.b64encode(self._mp3_bytes).decode("ascii")

    def tearDown(self) -> None:
        """Run the atexit hook so no temp files leak between tests."""
        _cleanup_unsupported_media_temp_files()

    @staticmethod
    def _blocks(data: str, media_type: str) -> list[DataBlock]:
        """Build one unsupported-media block."""
        return [
            DataBlock(
                source=Base64Source(
                    data=data,
                    media_type=media_type,
                ),
            ),
        ]

    @staticmethod
    def _saved_path(text: str) -> str:
        """Extract the saved path from a formatter reminder."""
        prefix = "saved locally at: "
        start = text.index(prefix) + len(prefix)
        return text[start : text.index(".</system-reminder>", start)]

    def test_same_base64_yields_deterministic_path(self) -> None:
        """Identical ``Base64Source`` bytes must produce identical output."""
        blocks = self._blocks(self._png_b64, "image/png")
        first, _ = self._fmt.convert_tool_result_to_string(blocks)
        second, _ = self._fmt.convert_tool_result_to_string(blocks)
        self.assertEqual(first, second)
        self.assertIn("saved locally at:", first)

    def test_written_file_contains_decoded_bytes(self) -> None:
        """The process-owned temp file must store the decoded bytes."""
        blocks = self._blocks(self._mp3_b64, "audio/mpeg")
        text, _ = self._fmt.convert_tool_result_to_string(blocks)
        path = self._saved_path(text)
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as handle:
            payload = handle.read()
        self.assertEqual(payload, self._mp3_bytes)

    def test_distinct_contents_yield_distinct_paths(self) -> None:
        """Different payloads must use different stable paths."""
        blocks_a = self._blocks(self._png_b64, "image/png")
        blocks_b = self._blocks(self._mp3_b64, "audio/mpeg")
        text_a, _ = self._fmt.convert_tool_result_to_string(blocks_a)
        text_b, _ = self._fmt.convert_tool_result_to_string(blocks_b)
        self.assertNotEqual(text_a, text_b)
        self.assertNotEqual(
            self._saved_path(text_a),
            self._saved_path(text_b),
        )

    def test_cache_directory_is_private_and_cleaned(self) -> None:
        """The cache must be process-private and removed during cleanup."""
        blocks = self._blocks(self._png_b64, "image/png")
        text, _ = self._fmt.convert_tool_result_to_string(blocks)
        path = self._saved_path(text)
        temp_dir = os.path.dirname(path)
        if os.name != "nt":
            self.assertEqual(
                stat.S_IMODE(os.stat(temp_dir).st_mode),
                0o700,
            )
        _cleanup_unsupported_media_temp_files()
        self.assertFalse(os.path.exists(temp_dir))

    def test_no_leaked_temp_file_per_call(self) -> None:
        """Five consecutive runs must leave only one temp payload file."""
        blocks = self._blocks(self._png_b64, "image/png")
        text = ""
        for _ in range(5):
            text, _ = self._fmt.convert_tool_result_to_string(blocks)
        temp_dir = os.path.dirname(self._saved_path(text))
        self.assertEqual(len(os.listdir(temp_dir)), 1)

    def test_cache_hit_skips_redundant_base64_decode(self) -> None:
        """A stable cache hit must not decode the payload again."""
        blocks = self._blocks(self._png_b64, "image/png")
        first, _ = self._fmt.convert_tool_result_to_string(blocks)
        with patch.object(
            formatter_base.base64,
            "b64decode",
            side_effect=AssertionError("unexpected decode"),
        ):
            second, _ = self._fmt.convert_tool_result_to_string(blocks)
        self.assertEqual(first, second)

    def test_replace_failure_is_propagated_without_invalid_path(self) -> None:
        """A failed atomic write must not return a missing target."""
        blocks = self._blocks(self._png_b64, "image/png")
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "agentscope.formatter._formatter_base."
                    "_get_unsupported_media_temp_dir",
                    return_value=temp_dir,
                ),
                patch.object(
                    formatter_base.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    self._fmt.convert_tool_result_to_string(blocks)
            self.assertEqual(os.listdir(temp_dir), [])

    def test_worker_exit_does_not_remove_another_worker_file(self) -> None:
        """Each process must clean only its own unsupported-media cache."""
        script = """
import base64
import sys

from agentscope.formatter._formatter_base import FormatterBase
from agentscope.message import Base64Source, DataBlock


class TextOnlyFormatter(FormatterBase):
    async def format(self, *args, **kwargs):
        return []


payload = base64.b64encode(b"shared payload").decode("ascii")
text, _ = TextOnlyFormatter().convert_tool_result_to_string(
    [
        DataBlock(
            source=Base64Source(
                data=payload,
                media_type="image/png",
            ),
        ),
    ],
)
path = text.split("saved locally at: ", 1)[1].split(
    ".</system-reminder>",
    1,
)[0]
print(path, flush=True)
sys.stdin.readline()
"""
        with ExitStack() as stack:
            workers = [
                stack.enter_context(
                    subprocess.Popen(
                        [sys.executable, "-c", script],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    ),
                )
                for _ in range(2)
            ]
            paths: list[str] = []
            try:
                for worker in workers:
                    self.assertIsNotNone(worker.stdout)
                    paths.append(worker.stdout.readline().strip())
                self.assertNotEqual(
                    os.path.dirname(paths[0]),
                    os.path.dirname(paths[1]),
                )
                self.assertTrue(all(os.path.isfile(path) for path in paths))

                self.assertIsNotNone(workers[0].stdin)
                workers[0].stdin.close()
                self.assertEqual(workers[0].wait(timeout=20), 0)
                self.assertFalse(os.path.exists(paths[0]))
                self.assertTrue(os.path.isfile(paths[1]))
            finally:
                for worker in workers:
                    if worker.poll() is None:
                        self.assertIsNotNone(worker.stdin)
                        worker.stdin.close()
                        worker.wait(timeout=20)
