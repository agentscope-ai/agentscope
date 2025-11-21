# -*- coding: utf-8 -*-
"""Utility functions for memory compression."""
import json
from typing import Sequence, Union

from pydantic import BaseModel, Field

from agentscope.message import Msg
from agentscope.token import TokenCounterBase


class MemoryCompressionSchema(BaseModel):
    """
    The schema for the memory compression.
    """

    compressed_text: str = Field(..., description="The compressed text")


def format_msgs(
    msgs: Union[Sequence[Msg], Msg],
) -> list[dict]:
    """Format a list of messages or memory units to a string in order.

    Args:
        msgs (Union[Sequence[Msg], Msg, Sequence[MemRecord], MemRecord]):
            the info to format
    Raises:
        ValueError: the message type or the content type is invalid

    Returns:
        str: the formatted messages
    """
    results = []
    if not isinstance(msgs, Sequence):
        msgs = [msgs]
    for msg in msgs:
        if not isinstance(msg, Msg):
            raise ValueError(f"Invalid message type: {type(msg)}")
        role = msg.role
        content = msg.content
        if isinstance(content, str):
            results.append(
                {
                    "role": role,
                    "content": content,
                },
            )
        elif isinstance(content, list):
            unit = {
                "role": role,
                "content": [],
            }
            for c in content:
                unit["content"].append(c)

            results.append(unit)
        else:
            raise ValueError(f"Invalid content type: {type(content)}")
    return results


async def count_words(
    token_counter: TokenCounterBase,
    text: str | list[dict],
) -> int:
    """Count the number of tokens using TokenCounter.count interface.

    Args:
        token_counter (TokenCounterBase):
            the token counter to use for counting tokens
        text (str|list[dict]):
            the text to count the number of tokens. If str, can be plain
            text or JSON string.

    Returns:
        int: the number of tokens in the text
    """
    if isinstance(text, list):
        # text is already a list of dicts
        messages = text
    elif isinstance(text, str):
        # text is a string - try to parse as JSON first
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                # It's a JSON array of messages
                messages = parsed
            else:
                # It's a JSON object or other type, wrap it
                messages = [{"role": "user", "content": text}]
        except (json.JSONDecodeError, TypeError):
            # Not valid JSON, treat as plain text
            messages = [{"role": "user", "content": text}]
    else:
        # Fallback: wrap in a message
        messages = [{"role": "user", "content": str(text)}]

    return await token_counter.count(messages)
