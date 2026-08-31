# -*- coding: utf-8 -*-
"""Shared utility helpers for model-examples scripts."""
import base64 as _b64
from typing import AsyncGenerator

from agentscope.message import (
    TextBlock,
    ThinkingBlock,
    DataBlock,
    Base64Source,
)
from agentscope.model import ChatResponse


async def stream_and_collect(
    gen: AsyncGenerator[ChatResponse, None],
) -> ChatResponse:
    """Stream delta chunks printing text in real-time; return the final chunk.

    Only delta chunks (is_last=False) are printed.  The final accumulated
    chunk (is_last=True) is returned so callers can read ToolCallBlock objects
    from it without re-printing the entire content.  Text from the final chunk
    is printed only when no text was streamed in any delta chunk (e.g. some
    models batch the answer in the last chunk).

    Streaming ``DataBlock`` chunks (e.g. omni audio output) are reported as
    per-chunk size summaries while the stream is being consumed, so callers
    can see the audio arriving incrementally rather than only in the final
    cumulative chunk.
    """
    final: ChatResponse | None = None
    in_thinking = False
    text_was_streamed = False
    # Track per-block audio progress: block_id -> (media_type, chunk_count,
    # total_bytes).
    audio_progress: dict[str, list] = {}
    async for chunk in gen:
        if chunk.is_last:
            final = chunk
            continue  # Skip printing; full content is in the final chunk
        for block in chunk.content:
            if isinstance(block, ThinkingBlock):
                if not in_thinking:
                    print("[Thinking] ", end="", flush=True)
                    in_thinking = True
                print(block.thinking, end="", flush=True)
            elif isinstance(block, TextBlock):
                if in_thinking:
                    print()  # Newline after thinking content
                    print("--- Answer ---")
                    in_thinking = False
                print(block.text, end="", flush=True)
                text_was_streamed = True
            elif isinstance(block, DataBlock) and isinstance(
                block.source,
                Base64Source,
            ):
                # Streaming binary delta (e.g. omni audio output).
                if in_thinking:
                    print()
                    print("--- Answer ---")
                    in_thinking = False
                delta_bytes = len(_b64.b64decode(block.source.data))
                state = audio_progress.setdefault(
                    block.id,
                    [block.source.media_type, 0, 0],
                )
                state[1] += 1
                state[2] += delta_bytes
                print(
                    f"\n[Audio chunk #{state[1]} ({state[0]}): "
                    f"+{delta_bytes}B, total={state[2]}B]",
                    flush=True,
                )
    # If text was not streamed in any delta, print it from the final chunk now.
    if not text_was_streamed and final is not None:
        final_text = "".join(
            b.text
            for b in final.content
            if isinstance(b, TextBlock) and b.text
        )
        if final_text:
            if in_thinking:
                print()
                print("--- Answer ---")
                in_thinking = False
            print(final_text)
    if in_thinking:
        print()
    # Report cumulative audio output from the final chunk.
    if final is not None:
        for block in final.content:
            if isinstance(block, DataBlock) and isinstance(
                block.source,
                Base64Source,
            ):
                media = block.source.media_type
                byte_size = len(_b64.b64decode(block.source.data))
                print(
                    f"[Audio output (cumulative): {media}, {byte_size} bytes]",
                )
    print()
    assert final is not None
    return final
