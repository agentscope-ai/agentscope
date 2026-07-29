# -*- coding: utf-8 -*-
"""Unit tests for :class:`agentscope.model.ChatModelBase.__call__` — the
retry / accumulation / interrupt wrapper around ``_call_api``."""

import asyncio
import base64
from unittest.async_case import IsolatedAsyncioTestCase

from pydantic import BaseModel

from utils import AnyString, MockModel

from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    URLSource,
    UserMsg,
)
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    ChatUsage,
    FinishedReason,
)
from agentscope.tool import ToolChoice


def _dump(chat_response: ChatResponse) -> dict:
    """Normalize a ``ChatResponse`` (a ``dict``-subclass with pydantic
    blocks inside) into a plain dict suitable for
    ``assertDictEqual`` / ``assertListEqual`` comparison."""
    d = dict(chat_response)
    d["content"] = [b.model_dump(mode="json") for b in d["content"]]
    if d["usage"] is not None:
        d["usage"] = dict(d["usage"])
    return d


def _expected(
    content: list,
    is_last: bool,
    finished_reason: FinishedReason = FinishedReason.COMPLETED,
    usage: dict | None = None,
) -> dict:
    """Build the expected serialized ``ChatResponse`` dict, with
    ``AnyString`` placeholders for auto-generated fields (id,
    created_at)."""
    content = [
        {"created_at": AnyString(), "finished_at": None, **b} for b in content
    ]
    return {
        "content": content,
        "is_last": is_last,
        "id": AnyString(),
        "created_at": AnyString(),
        "type": "chat_response",
        "usage": usage,
        "finished_reason": finished_reason,
        "metadata": {},
    }


class ChatModelBaseCallTest(IsolatedAsyncioTestCase):
    """Test ``ChatModelBase.__call__`` end-to-end with a ``MockModel``.

    Covers the four scenarios that ``__call__`` must handle:

    1. Non-stream success — the underlying ``ChatResponse`` is returned
       verbatim.
    2. Non-stream ``CancelledError`` raised from inside ``_call_api`` —
       converted to a ``ChatResponse`` with
       ``finished_reason=INTERRUPTED``.
    3. Stream success — every delta is forwarded, followed by an
       accumulated final ``ChatResponse`` with ``is_last=True`` and
       ``finished_reason=COMPLETED``.
    4. Stream ``CancelledError`` raised while consuming the underlying
       async generator — deltas produced so far are forwarded, followed
       by an accumulated final ``ChatResponse`` with
       ``finished_reason=INTERRUPTED``.
    """

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.model = MockModel(model="mock-model")
        self.messages = [UserMsg(name="user", content="hi")]

    # ------------------------------------------------------------------
    # 1) non-stream success
    # ------------------------------------------------------------------
    async def test_non_stream_success(self) -> None:
        """Non-stream ``_call_api`` returns a ``ChatResponse``; the base
        class must return it unchanged."""
        response = ChatResponse(
            content=[TextBlock(text="hello", id="t1")],
            is_last=True,
        )
        self.model.set_responses([response])

        result = await self.model(messages=self.messages)

        self.assertIsInstance(result, ChatResponse)
        self.assertDictEqual(
            _dump(result),
            _expected(
                content=[
                    {"type": "text", "text": "hello", "id": "t1"},
                ],
                is_last=True,
            ),
        )

    # ------------------------------------------------------------------
    # 2) non-stream CancelledError raised from inside _call_api
    # ------------------------------------------------------------------
    async def test_non_stream_cancelled_error(self) -> None:
        """``CancelledError`` raised from inside a non-stream
        ``_call_api`` is translated into an empty ``ChatResponse`` with
        ``finished_reason=INTERRUPTED``."""
        self.model.set_responses([asyncio.CancelledError()])

        result = await self.model(messages=self.messages)

        self.assertIsInstance(result, ChatResponse)
        self.assertDictEqual(
            _dump(result),
            _expected(
                content=[],
                is_last=True,
                finished_reason=FinishedReason.INTERRUPTED,
            ),
        )

    # ------------------------------------------------------------------
    # 3) stream success
    # ------------------------------------------------------------------
    async def test_stream_success_with_final_accumulation(self) -> None:
        """A well-behaved stream of deltas is forwarded chunk-by-chunk;
        the base class appends a final accumulated ``ChatResponse``
        with ``is_last=True``."""
        deltas = [
            ChatResponse(
                content=[TextBlock(text="hello", id="t1")],
                is_last=False,
                id="chunk-1",
            ),
            ChatResponse(
                content=[TextBlock(text=" world", id="t1")],
                is_last=False,
                id="chunk-2",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        collected = [_dump(c) async for c in gen]

        self.assertListEqual(
            collected,
            [
                # delta 1 — passed through verbatim
                _expected(
                    content=[
                        {"type": "text", "text": "hello", "id": "t1"},
                    ],
                    is_last=False,
                ),
                # delta 2 — passed through verbatim
                _expected(
                    content=[
                        {"type": "text", "text": " world", "id": "t1"},
                    ],
                    is_last=False,
                ),
                # accumulated final — synthesised by ChatModelBase
                _expected(
                    content=[
                        {
                            "type": "text",
                            "text": "hello world",
                            "id": "t1",
                        },
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.COMPLETED,
                ),
            ],
        )

    # ------------------------------------------------------------------
    # 4) stream CancelledError raised while consuming the generator
    # ------------------------------------------------------------------
    async def test_stream_cancelled_error_mid_consumption(self) -> None:
        """``CancelledError`` raised mid-stream is caught inside the
        base class's ``_stream`` wrapper. Deltas produced before the
        cancellation are still forwarded, and a final accumulated
        ``ChatResponse`` with ``finished_reason=INTERRUPTED`` is
        appended."""
        deltas = [
            ChatResponse(
                content=[TextBlock(text="hello", id="t1")],
                is_last=False,
                id="chunk-1",
            ),
            asyncio.CancelledError(),
            # anything after the exception must not be reached
            ChatResponse(
                content=[TextBlock(text=" world", id="t1")],
                is_last=False,
                id="chunk-2",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        collected = [_dump(c) async for c in gen]

        self.assertListEqual(
            collected,
            [
                # the one delta yielded before the cancellation
                _expected(
                    content=[
                        {"type": "text", "text": "hello", "id": "t1"},
                    ],
                    is_last=False,
                ),
                # accumulated final with INTERRUPTED — content reflects
                # only the deltas received before the cancellation
                _expected(
                    content=[
                        {"type": "text", "text": "hello", "id": "t1"},
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.INTERRUPTED,
                ),
            ],
        )

    # ------------------------------------------------------------------
    # 5) stream CancelledError — thinking + text content interrupted
    # ------------------------------------------------------------------
    async def test_stream_cancelled_error_with_thinking_and_text(
        self,
    ) -> None:
        """Same as (4) but the underlying stream produces multiple
        ``ThinkingBlock`` and ``TextBlock`` deltas (across the same
        block ids) before the cancellation. The accumulated final
        response must merge the deltas by block id in their original
        order."""
        deltas = [
            # thinking (part 1)
            ChatResponse(
                content=[
                    ThinkingBlock(thinking="let me ", id="think-1"),
                ],
                is_last=False,
                id="chunk-1",
            ),
            # thinking (part 2, same block-id, plus signature)
            ChatResponse(
                content=[
                    ThinkingBlock(thinking="think...", id="think-1"),
                ],
                is_last=False,
                id="chunk-2",
            ),
            # text (part 1, new block-id)
            ChatResponse(
                content=[TextBlock(text="hello", id="text-1")],
                is_last=False,
                id="chunk-3",
            ),
            asyncio.CancelledError(),
            # unreachable
            ChatResponse(
                content=[TextBlock(text=" world", id="text-1")],
                is_last=False,
                id="chunk-4",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        collected = [_dump(c) async for c in gen]

        self.assertListEqual(
            collected,
            [
                _expected(
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "let me ",
                            "id": "think-1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "think...",
                            "id": "think-1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {"type": "text", "text": "hello", "id": "text-1"},
                    ],
                    is_last=False,
                ),
                # accumulated final — thinking merged, text merged, no
                # trailing " world" (it came after the cancellation)
                _expected(
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "let me think...",
                            "id": "think-1",
                        },
                        {
                            "type": "text",
                            "text": "hello",
                            "id": "text-1",
                        },
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.INTERRUPTED,
                ),
            ],
        )

    # ------------------------------------------------------------------
    # 6) stream CancelledError — thinking + text + tool_call interrupted
    # ------------------------------------------------------------------
    async def test_stream_cancelled_error_with_thinking_text_tool_call(
        self,
    ) -> None:
        """Same as (5) but the stream also produces a
        ``ToolCallBlock`` whose ``input`` string is streamed across
        multiple deltas. The cancellation happens after the partial
        tool_call input; the accumulated final response must contain
        the tool_call with the concatenated ``input`` received so far."""
        deltas = [
            # thinking
            ChatResponse(
                content=[
                    ThinkingBlock(thinking="planning...", id="think-1"),
                ],
                is_last=False,
                id="chunk-1",
            ),
            # text
            ChatResponse(
                content=[
                    TextBlock(text="calling tool", id="text-1"),
                ],
                is_last=False,
                id="chunk-2",
            ),
            # tool_call (part 1: name + partial input)
            ChatResponse(
                content=[
                    ToolCallBlock(
                        id="tool-1",
                        name="get_weather",
                        input='{"city":"',
                    ),
                ],
                is_last=False,
                id="chunk-3",
            ),
            # tool_call (part 2: input continuation, same block-id)
            ChatResponse(
                content=[
                    ToolCallBlock(
                        id="tool-1",
                        name="get_weather",
                        input='Beijing"',
                    ),
                ],
                is_last=False,
                id="chunk-4",
            ),
            asyncio.CancelledError(),
            # unreachable — the closing "}" never arrives
            ChatResponse(
                content=[
                    ToolCallBlock(
                        id="tool-1",
                        name="get_weather",
                        input="}",
                    ),
                ],
                is_last=False,
                id="chunk-5",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        collected = [_dump(c) async for c in gen]

        self.assertListEqual(
            collected,
            [
                _expected(
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "planning...",
                            "id": "think-1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "text",
                            "text": "calling tool",
                            "id": "text-1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "get_weather",
                            "input": '{"city":"',
                            "state": "pending",
                            "suggested_rules": [],
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "get_weather",
                            "input": 'Beijing"',
                            "state": "pending",
                            "suggested_rules": [],
                        },
                    ],
                    is_last=False,
                ),
                # accumulated final — thinking / text preserved,
                # tool_call.input concatenated to the partial JSON
                # received before the cancellation (no closing "}")
                _expected(
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "planning...",
                            "id": "think-1",
                        },
                        {
                            "type": "text",
                            "text": "calling tool",
                            "id": "text-1",
                        },
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "get_weather",
                            "input": '{"city":"Beijing"',
                            "state": "pending",
                            "suggested_rules": [],
                        },
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.INTERRUPTED,
                ),
            ],
        )

    # ------------------------------------------------------------------
    # 7) stream with large tool call arguments — O(n) accumulation
    # ------------------------------------------------------------------
    async def test_stream_large_tool_call_linear_accumulation(
        self,
    ) -> None:
        """A large tool call argument (simulating write_file with many
        fragments) must be accumulated in O(n) time. We verify the
        final accumulated content is correct with 10000 fragments."""
        num_chunks = 10000
        fragment = "x" * 100  # 100 chars per chunk

        deltas = [
            ChatResponse(
                content=[
                    ToolCallBlock(
                        id="tool-big",
                        name="write_file",
                        input=fragment,
                    ),
                ],
                is_last=False,
                id=f"chunk-{i}",
            )
            for i in range(num_chunks)
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        last_chunk = None
        async for chunk in gen:
            last_chunk = chunk

        self.assertDictEqual(
            _dump(last_chunk),
            _expected(
                content=[
                    {
                        "type": "tool_call",
                        "id": "tool-big",
                        "name": "write_file",
                        "input": fragment * num_chunks,
                        "state": "pending",
                        "suggested_rules": [],
                    },
                ],
                is_last=True,
                finished_reason=FinishedReason.COMPLETED,
            ),
        )

    # ------------------------------------------------------------------
    # 8) stream with is_last=True — no acc_res needed
    # ------------------------------------------------------------------
    async def test_stream_with_final_chunk_no_acc_res(self) -> None:
        """When the model stream produces a final chunk with
        is_last=True, acc_res should NOT be yielded (the model
        provides the complete response itself)."""
        deltas = [
            ChatResponse(
                content=[TextBlock(text="hello", id="t1")],
                is_last=False,
                id="chunk-1",
            ),
            ChatResponse(
                content=[TextBlock(text=" world", id="t1")],
                is_last=False,
                id="chunk-2",
            ),
            # Model provides its own final complete response
            ChatResponse(
                content=[TextBlock(text="hello world", id="t1")],
                is_last=True,
                id="chunk-final",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        collected = [_dump(c) async for c in gen]

        # Only 3 chunks: 2 deltas + 1 model-provided final
        # (NOT 4 — no acc_res appended)
        self.assertListEqual(
            collected,
            [
                _expected(
                    content=[
                        {"type": "text", "text": "hello", "id": "t1"},
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "text",
                            "text": " world",
                            "id": "t1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "text",
                            "text": "hello world",
                            "id": "t1",
                        },
                    ],
                    is_last=True,
                ),
            ],
        )

    # ------------------------------------------------------------------
    # 9) stream mixed block types — normal completion (happy path)
    # ------------------------------------------------------------------
    async def test_stream_mixed_blocks_normal_completion(self) -> None:
        """Mixed block types (thinking + text + tool_call) in a
        normal stream completion (no CancelledError). The final
        accumulated response must contain all blocks in order with
        correctly joined fragments."""
        deltas = [
            # thinking part 1
            ChatResponse(
                content=[
                    ThinkingBlock(thinking="step 1: ", id="think-1"),
                ],
                is_last=False,
                id="chunk-1",
            ),
            # thinking part 2
            ChatResponse(
                content=[
                    ThinkingBlock(thinking="analyze", id="think-1"),
                ],
                is_last=False,
                id="chunk-2",
            ),
            # text part 1
            ChatResponse(
                content=[TextBlock(text="I will ", id="text-1")],
                is_last=False,
                id="chunk-3",
            ),
            # text part 2
            ChatResponse(
                content=[TextBlock(text="help you", id="text-1")],
                is_last=False,
                id="chunk-4",
            ),
            # tool_call part 1
            ChatResponse(
                content=[
                    ToolCallBlock(
                        id="tool-1",
                        name="search",
                        input='{"query":',
                    ),
                ],
                is_last=False,
                id="chunk-5",
            ),
            # tool_call part 2
            ChatResponse(
                content=[
                    ToolCallBlock(
                        id="tool-1",
                        name="search",
                        input='"hello"}',
                    ),
                ],
                is_last=False,
                id="chunk-6",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        collected = [_dump(c) async for c in gen]

        self.assertListEqual(
            collected,
            [
                _expected(
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "step 1: ",
                            "id": "think-1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "analyze",
                            "id": "think-1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "text",
                            "text": "I will ",
                            "id": "text-1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "text",
                            "text": "help you",
                            "id": "text-1",
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "search",
                            "input": '{"query":',
                            "state": "pending",
                            "suggested_rules": [],
                        },
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "search",
                            "input": '"hello"}',
                            "state": "pending",
                            "suggested_rules": [],
                        },
                    ],
                    is_last=False,
                ),
                # accumulated final — all blocks merged
                _expected(
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "step 1: analyze",
                            "id": "think-1",
                        },
                        {
                            "type": "text",
                            "text": "I will help you",
                            "id": "text-1",
                        },
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "search",
                            "input": '{"query":"hello"}',
                            "state": "pending",
                            "suggested_rules": [],
                        },
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.COMPLETED,
                ),
            ],
        )

    # ------------------------------------------------------------------
    # 10) stream audio data block — raw bytes joined, encoded once
    # ------------------------------------------------------------------
    async def test_stream_audio_data_block_accumulation(self) -> None:
        """Audio deltas must accumulate as raw bytes. Joining the base64
        strings instead would silently truncate at the first padded
        fragment, so the decoded result is checked against the
        concatenated raw bytes."""
        raw = [b"ab", b"cd", b"ef"]  # each encodes to a '='-padded string
        deltas = [
            ChatResponse(
                content=[
                    DataBlock(
                        id="audio-1",
                        source=Base64Source(
                            data=base64.b64encode(part).decode("ascii"),
                            media_type="audio/wav",
                        ),
                        # Providers name the asset on the opening delta
                        name="out.wav" if i == 0 else None,
                    ),
                ],
                is_last=False,
                id=f"chunk-{i}",
            )
            for i, part in enumerate(raw)
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        last_chunk = None
        async for chunk in gen:
            last_chunk = chunk

        self.assertDictEqual(
            _dump(last_chunk),
            _expected(
                content=[
                    {
                        "type": "data",
                        "id": "audio-1",
                        "source": {
                            "type": "base64",
                            "data": base64.b64encode(b"".join(raw)).decode(
                                "ascii",
                            ),
                            "media_type": "audio/wav",
                        },
                        "name": "out.wav",
                    },
                ],
                is_last=True,
                finished_reason=FinishedReason.COMPLETED,
            ),
        )

    # ------------------------------------------------------------------
    # 11) stream non-audio data block — the latest delta wins
    # ------------------------------------------------------------------
    async def test_stream_non_audio_data_block_latest_wins(self) -> None:
        """Non-audio media are standalone assets rather than streamable
        deltas, so byte concatenation is meaningless and the latest
        delta must replace the previous one."""
        deltas = [
            ChatResponse(
                content=[
                    DataBlock(
                        id="img-1",
                        source=Base64Source(
                            data="AAAA",
                            media_type="image/png",
                        ),
                    ),
                ],
                is_last=False,
                id="chunk-1",
            ),
            ChatResponse(
                content=[
                    DataBlock(
                        id="img-1",
                        source=Base64Source(
                            data="BBBB",
                            media_type="image/png",
                        ),
                    ),
                ],
                is_last=False,
                id="chunk-2",
            ),
            ChatResponse(
                content=[
                    DataBlock(
                        id="url-1",
                        source=URLSource(
                            url="https://example.com/a.png",
                            media_type="image/png",
                        ),
                    ),
                ],
                is_last=False,
                id="chunk-3",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        last_chunk = None
        async for chunk in gen:
            last_chunk = chunk

        self.assertDictEqual(
            _dump(last_chunk),
            _expected(
                content=[
                    {
                        "type": "data",
                        "id": "img-1",
                        "source": {
                            "type": "base64",
                            "data": "BBBB",
                            "media_type": "image/png",
                        },
                        "name": None,
                    },
                    {
                        "type": "data",
                        "id": "url-1",
                        "source": {
                            "type": "url",
                            "url": "https://example.com/a.png",
                            "media_type": "image/png",
                        },
                        "name": None,
                    },
                ],
                is_last=True,
                finished_reason=FinishedReason.COMPLETED,
            ),
        )

    # ------------------------------------------------------------------
    # 12) stream data block whose media type switches mid-stream
    # ------------------------------------------------------------------
    async def test_stream_data_block_media_type_switch(self) -> None:
        """A media type change makes the accumulated fragments
        incompatible with the incoming delta, so they are dropped and
        the latest delta wins."""
        deltas = [
            ChatResponse(
                content=[
                    DataBlock(
                        id="audio-1",
                        source=Base64Source(
                            data=base64.b64encode(b"old").decode("ascii"),
                            media_type="audio/wav",
                        ),
                    ),
                ],
                is_last=False,
                id="chunk-1",
            ),
            ChatResponse(
                content=[
                    DataBlock(
                        id="audio-1",
                        source=Base64Source(
                            data=base64.b64encode(b"new").decode("ascii"),
                            media_type="audio/mpeg",
                        ),
                    ),
                ],
                is_last=False,
                id="chunk-2",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        last_chunk = None
        async for chunk in gen:
            last_chunk = chunk

        self.assertDictEqual(
            _dump(last_chunk),
            _expected(
                content=[
                    {
                        "type": "data",
                        "id": "audio-1",
                        "source": {
                            "type": "base64",
                            "data": base64.b64encode(b"new").decode("ascii"),
                            "media_type": "audio/mpeg",
                        },
                        "name": None,
                    },
                ],
                is_last=True,
                finished_reason=FinishedReason.COMPLETED,
            ),
        )

    # ------------------------------------------------------------------
    # 13) stream thinking whose signature arrives on the closing delta
    # ------------------------------------------------------------------
    async def test_stream_thinking_signature_on_closing_delta(self) -> None:
        """Provider-specific extras (e.g. Anthropic's ``signature``) are
        often emitted only on the closing delta and must survive into
        the accumulated block."""
        deltas = [
            ChatResponse(
                content=[ThinkingBlock(thinking="step ", id="think-1")],
                is_last=False,
                id="chunk-1",
            ),
            ChatResponse(
                content=[ThinkingBlock(thinking="one", id="think-1")],
                is_last=False,
                id="chunk-2",
            ),
            ChatResponse(
                content=[
                    ThinkingBlock(
                        thinking="",
                        id="think-1",
                        signature="sig-abc",
                    ),
                ],
                is_last=False,
                id="chunk-3",
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        last_chunk = None
        async for chunk in gen:
            last_chunk = chunk

        self.assertDictEqual(
            _dump(last_chunk),
            _expected(
                content=[
                    {
                        "type": "thinking",
                        "thinking": "step one",
                        "id": "think-1",
                        "signature": "sig-abc",
                    },
                ],
                is_last=True,
                finished_reason=FinishedReason.COMPLETED,
            ),
        )

    # ------------------------------------------------------------------
    # 14) stream usage carried by a trailing usage-only chunk
    # ------------------------------------------------------------------
    async def test_stream_usage_absorbed_into_accumulated(self) -> None:
        """OpenAI-compatible APIs emit a trailing usage-only chunk with
        no content. It must not be surfaced to the consumer, but its
        usage must land on the accumulated response."""
        deltas = [
            ChatResponse(
                content=[TextBlock(text="hello", id="t1")],
                is_last=False,
                id="chunk-1",
            ),
            ChatResponse(
                content=[],
                is_last=False,
                id="chunk-2",
                usage=ChatUsage(input_tokens=3, output_tokens=7, time=0.5),
            ),
        ]
        self.model.set_responses([deltas])

        gen = await self.model(messages=self.messages)
        collected = [_dump(c) async for c in gen]

        # The usage-only carrier chunk is absorbed, not forwarded
        self.assertListEqual(
            collected,
            [
                _expected(
                    content=[
                        {"type": "text", "text": "hello", "id": "t1"},
                    ],
                    is_last=False,
                ),
                _expected(
                    content=[
                        {"type": "text", "text": "hello", "id": "t1"},
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.COMPLETED,
                    usage={
                        "input_tokens": 3,
                        "output_tokens": 7,
                        "time": 0.5,
                        "cache_creation_input_tokens": 0,
                        "cache_input_tokens": 0,
                        "type": "chat",
                        "metadata": None,
                    },
                ),
            ],
        )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""


class _SummaryModel(BaseModel):
    """Pydantic structured model used by the structured-output tests."""

    summary: str


class _ToolCallRejectingMockModel(MockModel):
    """A mock model that drives ``ChatModelBase._call_api`` via the
    recording of :meth:`MockModel.set_responses`.

    Unlike :class:`MockModel`, its subclass override of
    ``_call_api_with_structured_output`` is intentionally removed, so
    calls flow straight into :class:`ChatModelBase`'s implementation and
    exercise the forced/auto tool_choice fallback plus JSON-in-TextBlock
    extraction.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Disable the MockModel structured-output shortcut."""

        super().__init__(*args, **kwargs)
        self.used_tool_choices: list[ToolChoice] = []

    async def _call_api(
        self,
        *args: object,
        **kwargs: object,
    ) -> object:
        tool_choice = kwargs.get("tool_choice")
        if isinstance(tool_choice, ToolChoice):
            self.used_tool_choices.append(tool_choice)
        return await MockModel._call_api(self, *args, **kwargs)

    async def _call_api_with_structured_output(
        self,
        *args: object,
        **kwargs: object,
    ) -> object:
        """Delegate straight to the base implementation, bypassing
        :class:`MockModel`'s shortcut that returns a pre-set
        ``StructuredResponse`` without exercising the real logic."""

        # pylint: disable-next=protected-access
        return await ChatModelBase._call_api_with_structured_output(
            self,
            *args,
            **kwargs,
        )


class ChatModelBaseStructuredOutputTest(IsolatedAsyncioTestCase):
    """Tests for ``ChatModelBase._call_api_with_structured_output``.

    Covers the three fallback paths introduced alongside issue #2137:

    1. Forced tool_choice succeeds via a normal ``ToolCallBlock`` — the
       base implementation should behave exactly as before.
    2. Forced tool_choice fails because the provider rejects it
       synchronously with a provider-level error — we must retry with
       ``tool_choice=auto`` instead of raising.
    3. Even under auto (or forced) tool_choice the provider emits a
       plain ``TextBlock`` with valid JSON inside — we must extract and
       validate that JSON instead of failing with "structured output
       failed".
    4. Streaming — when the provider produces a stream of
       ``ToolCallBlock`` deltas the base implementation must accumulate
       them into a single validated structured output.
    """

    async def asyncSetUp(self) -> None:
        """The async setup method."""

        self.model = _ToolCallRejectingMockModel(model="mock-model")
        self.messages = [UserMsg(name="user", content="please compress.")]
        self.model_name = "mock-model"

    # ------------------------------------------------------------------
    # 1) forced tool_choice ToolCallBlock — baseline behaviour
    # ------------------------------------------------------------------
    async def test_forced_tool_choice_extracts_tool_call_block(self) -> None:
        """Forced tool_choice returns a ToolCallBlock; the structured
        response must use its validated input dict."""

        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="tc-1",
                            name="generate_structured_output",
                            input='{"summary":"short"}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )

        result = await self.model.generate_structured_output(
            messages=self.messages,
            structured_model=_SummaryModel,
        )

        self.assertDictEqual(
            result.content,
            {"summary": "short"},
        )
        # Exactly one API call was made, and it used the forced
        # tool_choice (name of the generated tool).
        self.assertEqual(len(self.model.used_tool_choices), 1)
        self.assertEqual(
            self.model.used_tool_choices[0].mode,
            "generate_structured_output",
        )

    # ------------------------------------------------------------------
    # 2) forced tool_choice raises provider error → retry with auto
    # ------------------------------------------------------------------
    async def test_provider_error_forced_falls_back_to_auto(self) -> None:
        """A non-retryable provider-level error on the forced attempt
        should trigger a single retry with ``tool_choice=auto``. When
        the retry succeeds, the structured output from the retry wins.
        """

        class _ProviderBadRequest(RuntimeError):
            """Marker class – anything not in the retryable set retries
            with tool_choice=auto."""

        self.model.set_responses(
            [
                _ProviderBadRequest("tool_choice is not supported"),
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="tc-2",
                            name="generate_structured_output",
                            input='{"summary":"via auto"}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )

        result = await self.model.generate_structured_output(
            messages=self.messages,
            structured_model=_SummaryModel,
        )

        self.assertDictEqual(result.content, {"summary": "via auto"})
        self.assertEqual(len(self.model.used_tool_choices), 2)
        self.assertEqual(
            self.model.used_tool_choices[0].mode,
            "generate_structured_output",
        )
        self.assertEqual(self.model.used_tool_choices[1].mode, "auto")

    # ------------------------------------------------------------------
    # 3) auto tool_choice returns JSON inside a TextBlock — extraction
    # ------------------------------------------------------------------
    async def test_text_block_json_extraction_succeeds(self) -> None:
        """Thinking-mode providers often respond with JSON directly
        inside a TextBlock (no ToolCallBlock). The safety-net parser
        should extract it and validate it against the schema."""

        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        TextBlock(
                            text=(
                                "Here is my summary, wrapped as requested:\n"
                                "```json\n"
                                '{"summary": "hello from text block"}\n'
                                "```\n"
                            ),
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )

        result = await self.model.generate_structured_output(
            messages=self.messages,
            structured_model=_SummaryModel,
            tool_choice=ToolChoice(mode="auto"),
        )

        self.assertDictEqual(
            result.content,
            {"summary": "hello from text block"},
        )

    # ------------------------------------------------------------------
    # 4) stream of ToolCallBlock deltas accumulates correctly
    # ------------------------------------------------------------------
    async def test_streamed_tool_call_accumulates(self) -> None:
        """When the provider streams the structured output in fragments
        we must assemble them in the accumulator and validate the
        concatenated JSON against the schema."""

        stream_model = _ToolCallRejectingMockModel(
            model="mock-model",
            stream=True,
        )
        stream_model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="tc-s",
                                name="generate_structured_output",
                                input='{"summary":"par',
                            ),
                        ],
                        is_last=False,
                        id="chunk-1",
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="tc-s",
                                name="generate_structured_output",
                                input='tial stream"}',
                            ),
                        ],
                        is_last=False,
                        id="chunk-2",
                    ),
                ],
            ],
        )

        result = await stream_model.generate_structured_output(
            messages=self.messages,
            structured_model=_SummaryModel,
        )

        self.assertDictEqual(
            result.content,
            {"summary": "partial stream"},
        )
