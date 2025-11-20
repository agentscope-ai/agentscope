# -*- coding: utf-8 -*-
# pylint: disable=C0301, C0411, R1702, C0302, R0912, R0915, W1203, R0904, W0622
# pylint: disable=R1728
"""Utility functions and constants for ReActMemory."""

import json
import re
import asyncio
from typing import (
    Any,
    Awaitable,
    Callable,
    List,
    Literal,
    Optional,
    Sequence,
    Union,
    cast,
)
from agentscope.message import Msg
from agentscope.token import TokenCounterBase
from ._mem_record import MemRecord  # pylint: disable=relative-beyond-top-level


# Default values
DEFAULT_MAX_CHAT_HISTORY_LEN = 28000
DEFAULT_RETURN_CHAT_HISTORY_LEN = 28000
MAX_CHUNK_SIZE = 7000
MAX_EMBEDDING_SIZE = 8000
OVERLAP_SIZE = 500
ALLOWED_MAX_TOOL_RESULT_LEN = 5000
DEFAULT_MAX_MEMORY_LEN = 28000
MAX_CHAT_MODEL_TOKEN_SIZE = 28000


def _split_text_with_regex(
    text: str,
    separator: str,
    keep_separator: Union[bool, Literal["start", "end"]],
) -> List[str]:
    """Split text with regex separator."""
    # Now that we have the separator, split the text
    if separator:
        if keep_separator:
            # The parentheses in the pattern keep the delimiters in the result.
            _splits = re.split(f"({separator})", text)
            splits = (
                (
                    [
                        _splits[i] + _splits[i + 1]
                        for i in range(0, len(_splits) - 1, 2)
                    ]
                )
                if keep_separator == "end"
                else (
                    [
                        _splits[i] + _splits[i + 1]
                        for i in range(1, len(_splits), 2)
                    ]
                )
            )
            if len(_splits) % 2 == 0:
                splits += _splits[-1:]
            splits = (
                (splits + [_splits[-1]])
                if keep_separator == "end"
                else ([_splits[0]] + splits)
            )
        else:
            splits = re.split(separator, text)
    else:
        splits = list(text)
    return [s for s in splits if s != ""]


def _merge_splits(
    splits: List[str],
    separator: str,
    chunk_size: int,
    chunk_overlap: int,
    length_function: Callable[[str], int],
) -> List[str]:
    """Merge splits into chunks with overlap."""
    separator_len = length_function(separator)

    docs = []
    current_doc: List[str] = []
    total = 0
    for d in splits:
        _len = length_function(d)
        if (
            total + _len + (separator_len if len(current_doc) > 0 else 0)
            > chunk_size
        ):
            if total > chunk_size:
                import logging

                logging.warning(
                    f"Created a chunk of size {total}, "
                    f"which is larger than the specified {chunk_size}",
                )
            if current_doc:
                doc = separator.join(current_doc).strip()
                if doc:
                    docs.append(doc)
                # Keep on popping if:
                # - we have a larger chunk than in the chunk overlap
                # - or if we still have any chunks and the length is long
                while total > chunk_overlap or (
                    total
                    + _len
                    + (separator_len if len(current_doc) > 0 else 0)
                    > chunk_size
                    and total > 0
                ):
                    total -= length_function(current_doc[0]) + (
                        separator_len if len(current_doc) > 1 else 0
                    )
                    current_doc.pop(0)
        current_doc.append(d)
        total += _len + (separator_len if len(current_doc) > 1 else 0)
    doc = separator.join(current_doc).strip()
    if doc:
        docs.append(doc)
    return docs


def create_recursive_text_splitter(
    chunk_size: int = 4000,
    chunk_overlap: int = 200,
    length_function: Optional[
        Union[Callable[[str], int], Callable[[str], Awaitable[int]]]
    ] = None,
    separators: Optional[List[str]] = None,
    keep_separator: Union[bool, Literal["start", "end"]] = True,
    is_separator_regex: bool = False,
) -> Callable[[str], Awaitable[List[str]]]:
    """Create a recursive text splitter function.

    Returns a function that splits text into chunks based on the
    provided configuration.

    Args:
        chunk_size: Maximum size of each chunk
        chunk_overlap: Overlap size between chunks
        length_function: Function to calculate text length
            (can be sync or async, default: len)
        separators: List of separators to try
            (default: ["\n\n", "\n", " ", ""])
        keep_separator: Whether to keep separators in the output
        is_separator_regex: Whether separators are regex patterns

    Returns:
        A function that takes text and returns a list of text chunks
    """
    if length_function is None:
        length_function = len
    separators = separators or ["\n\n", "\n", " ", ""]

    # Check if length_function is async by inspecting if it's a
    # coroutine function
    import inspect

    is_async_length = inspect.iscoroutinefunction(length_function)

    # Create sync wrapper for _merge_splits if needed
    if is_async_length:

        def sync_length_func(text: str) -> int:
            """Synchronous wrapper for async length function."""
            try:
                # Try to get running loop - if this succeeds, we're in async
                # context and cannot use asyncio.run
                asyncio.get_running_loop()
                # We're in an async context, but _merge_splits needs sync
                # This should not happen in practice, but handle it
                raise RuntimeError(
                    "Cannot use async length_function in sync context",
                )
            except RuntimeError as e:
                # Check if it's our error (re-raise) or "no running loop" error
                if "Cannot use async length_function" in str(e):
                    raise
                # No running loop (get_running_loop raised RuntimeError),
                # can use asyncio.run
                coro = length_function(text)
                # Type checker doesn't know it's always a coroutine here
                if isinstance(coro, asyncio.Coroutine):
                    return asyncio.run(coro)
                # If not a coroutine, it must be an int (sync function)
                assert isinstance(coro, int), "Expected int or coroutine"
                return coro

    else:
        sync_length_func = length_function

    async def _split_text(text: str, separators: List[str]) -> List[str]:
        """Split incoming text and return chunks."""
        final_chunks = []
        # Get appropriate separator to use
        separator = separators[-1]
        new_separators = []
        for i, _s in enumerate(separators):
            _separator = _s if is_separator_regex else re.escape(_s)
            if _s == "":
                separator = _s
                break
            if re.search(_separator, text):
                separator = _s
                new_separators = separators[i + 1 :]
                break

        _separator = separator if is_separator_regex else re.escape(separator)
        splits = _split_text_with_regex(text, _separator, keep_separator)

        # Now go merging things, recursively splitting longer texts.
        _good_splits = []
        _separator = "" if keep_separator else separator
        for s in splits:
            if is_async_length:
                # length_function is a coroutine function, so await it
                # Type narrowing: if is_async_length is True, length_function
                # must be Callable[[str], Awaitable[int]]
                result = cast(
                    Callable[[str], Awaitable[int]],
                    length_function,
                )(s)
                total = await result
            else:
                # length_function is sync, so call it directly
                # Type narrowing: if is_async_length is False, length_function
                # must be Callable[[str], int]
                total = cast(Callable[[str], int], length_function)(s)
            if total < chunk_size:
                _good_splits.append(s)
            else:
                if _good_splits:
                    merged_text = _merge_splits(
                        _good_splits,
                        _separator,
                        chunk_size,
                        chunk_overlap,
                        sync_length_func,
                    )
                    final_chunks.extend(merged_text)
                    _good_splits = []
                if not new_separators:
                    final_chunks.append(s)
                else:
                    other_info = await _split_text(s, new_separators)
                    final_chunks.extend(other_info)
        if _good_splits:
            merged_text = _merge_splits(
                _good_splits,
                _separator,
                chunk_size,
                chunk_overlap,
                sync_length_func,
            )
            final_chunks.extend(merged_text)
        return final_chunks

    async def split_text(text: str) -> List[str]:
        """Split text into chunks."""
        return await _split_text(text, separators)

    return split_text


def time_order_check(unit: Sequence[Any]) -> bool:
    """Check if the unit is in time order."""

    def get_time(unit: Any) -> str:
        if isinstance(unit, MemRecord):
            return unit.metadata.get("last_modified_timestamp", None)
        else:
            try:
                return unit.payload.get("last_modified_timestamp", None)
            except Exception as e:
                raise ValueError(
                    f"Invalid unit type: {type(unit)}, " f"error: {e}",
                ) from e

    for i in range(len(unit) - 1):
        time_i, time_i_1 = get_time(unit[i]), get_time(unit[i + 1])
        if time_i is not None and time_i_1 is not None and time_i > time_i_1:
            return False
    return True


def format_msgs(
    msgs: Union[Sequence[Msg], Msg, Sequence[MemRecord], MemRecord],
    with_id: bool = True,
) -> str:
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
    for idx, msg in enumerate(msgs):
        if not isinstance(msg, Msg) and not isinstance(msg, MemRecord):
            raise ValueError(f"Invalid message type: {type(msg)}")
        if isinstance(msg, MemRecord):
            results.append(
                {
                    "role": msg.metadata.get("role", "assistant"),
                    "content": msg.metadata.get("data", None),
                },
            )
            if with_id:
                results[-1]["id"] = idx
        else:
            role = msg.role
            content = msg.content
            if isinstance(content, str):
                results.append(
                    {
                        "role": role,
                        "content": content,
                    },
                )
                if with_id:
                    results[-1]["id"] = idx
            elif isinstance(content, list):
                unit = {
                    "role": role,
                    "content": [],
                }
                if with_id:
                    unit["id"] = idx
                for c in content:
                    unit["content"].append(c)
                if unit["role"] == "system":
                    unit["role"] = "assistant"
                results.append(unit)
            else:
                raise ValueError(f"Invalid content type: {type(content)}")
    return json.dumps(results)


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
    print(f"Counting words for text: {text}")
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


def no_user_msg(mem: MemRecord) -> bool:
    """Filter function: remove msgs from user"""
    return mem.metadata.get("role", "assistant") not in [
        "user",
    ]
