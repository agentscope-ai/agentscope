# -*- coding: utf-8 -*-
"""Shared utility helpers for model-examples scripts."""
from typing import AsyncGenerator

from agentscope.message import TextBlock, ThinkingBlock
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
    """
    final: ChatResponse | None = None
    in_thinking = False
    text_was_streamed = False
    async for chunk in gen:
        if chunk.is_last:
            final = chunk
            continue  # Skip printing; full content is in the final chunk
        for block in chunk.content:
            if isinstance(block, ThinkingBlock):
                if not block.thinking:
                    continue  # Skip empty delta blocks
                if not in_thinking:
                    print("[Thinking] ", end="", flush=True)
                    in_thinking = True
                print(block.thinking, end="", flush=True)
            elif isinstance(block, TextBlock):
                if not block.text:
                    continue  # Skip empty delta blocks
                if in_thinking:
                    print()  # Newline after thinking content
                    print("--- Answer ---")
                    in_thinking = False
                print(block.text, end="", flush=True)
                text_was_streamed = True
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
    print()
    assert final is not None
    return final
