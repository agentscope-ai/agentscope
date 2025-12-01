# -*- coding: utf-8 -*-
"""Utility functions for memory compression."""
import json
from typing import Any, Sequence, Union

from pydantic import BaseModel, Field

from agentscope.message import Msg
from agentscope.token import TokenCounterBase


class MemoryCompressionSchema(BaseModel):
    """
    The schema for the memory compression.
    """

    compressed_text: str = Field(..., description="The compressed text")


class MemoryWithCompressionState(BaseModel):
    """
    The state for the memory with compress memory.
    """

    max_token: int = Field(
        ...,
        description="The maximum token count for memories in memory_storage",
    )
    chat_history_storage: list[dict[str, Any]] = Field(
        ...,
        description=(
            "The chat history storage in the current conversation session"
        ),
    )
    memory_storage: list[dict[str, Any]] = Field(
        ...,
        description=(
            "The compressed messages storage in the current "
            "conversation session"
        ),
    )


# Default compression prompt template
# Placeholders: {max_token}, {messages_list_json}, {schema_json}
DEFAULT_COMPRESSION_PROMPT_TEMPLATE = (
    "You are a memory compression assistant. Please summarize and "
    "compress the following conversation history into a concise "
    "summary that preserves the key information. \n\n You should "
    "compress the conversation into less than {max_token} "
    "tokens. The summary should be in the following json format:\n\n"
    "{schema_json}"
    "\n\nThe conversation history is:\n\n{messages_list_json}"
)


def format_msgs(
    msgs: Union[Sequence[Msg], Msg],
) -> list[dict]:
    """Format a list of messages to a list of dicts in order.
    Args:
        msgs (`Union[Sequence[Msg], Msg]`):
            The messages to format. Only `Msg` objects are accepted.

    Raises:
        ValueError: The message type or the content type is invalid.

    Returns:
        `list[dict]`: The formatted messages.
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
    token_counter: TokenCounterBase | None,
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

    # if token_counter is None, count the number of tokens in the messages
    if token_counter is None:
        return len(json.dumps(messages, ensure_ascii=False))

    # if token_counter is not None, count the number of tokens in the messages
    return await token_counter.count(messages)
