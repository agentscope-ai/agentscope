# -*- coding: utf-8 -*-
"""In-memory searchable storage for memory offloading.

This module provides a default, dependency-free implementation of
`MemoryOffloadingBase` that stores offloaded memory chunks in a
Python list and searches via substring matching.

Suitable for single-session use, prototyping, and testing. For
production use with large memory volumes, consider implementing a
backend based on vector databases or embedding-based search.
"""
import datetime
from dataclasses import dataclass, field
from typing import Any

from ...message import Msg
from ._offloading_base import MemoryOffloadingBase


@dataclass
class OffloadedChunk:
    """A chunk of offloaded compressed memory."""

    summary: str
    """The LLM-generated summary of the compressed messages."""

    messages: list[Msg]
    """The original messages that were compressed."""

    timestamp: str
    """ISO-format timestamp when the chunk was offloaded."""

    keywords: list[str] = field(default_factory=list)
    """Extracted keywords for search (auto-generated from summary)."""


class InMemorySearchableStorage(MemoryOffloadingBase):
    """Default in-memory implementation of memory offloading storage.

    Stores offloaded chunks in a Python list and performs substring-based
    search across summaries and message content. No external dependencies
    are required.

    Example:
        .. code-block:: python

            from agentscope.memory import InMemorySearchableStorage

            storage = InMemorySearchableStorage()

            # Store compressed messages
            await storage.store(
                msgs=[msg1, msg2, msg3],
                summary="User asked about Python async patterns",
            )

            # Search offloaded memory
            results = await storage.search("async", limit=3)
            for result in results:
                print(result["summary"])
    """

    def __init__(self) -> None:
        """Initialize the in-memory searchable storage."""
        self._chunks: list[OffloadedChunk] = []

    async def store(
        self,
        msgs: list[Msg],
        summary: str,
        **kwargs: Any,
    ) -> None:
        """Store compressed messages and their summary.

        Args:
            msgs (`list[Msg]`):
                The original messages that were compressed.
            summary (`str`):
                The LLM-generated summary of the compressed messages.
            **kwargs (`Any`):
                Additional keyword arguments (unused in this backend).
        """
        chunk = OffloadedChunk(
            summary=summary,
            messages=list(msgs),
            timestamp=datetime.datetime.now().isoformat(),
        )
        self._chunks.append(chunk)

    async def search(
        self,
        query: str,
        limit: int = 5,
        **kwargs: Any,
    ) -> list[dict]:
        """Search offloaded memories by substring matching.

        Searches across both the summary text and the text content of
        the original messages. Results are returned in reverse
        chronological order (most recent first).

        Args:
            query (`str`):
                The search query string. Case-insensitive substring
                matching is used.
            limit (`int`, optional):
                Maximum number of results to return. Defaults to 5.
            **kwargs (`Any`):
                Additional keyword arguments (unused in this backend).

        Returns:
            `list[dict]`:
                A list of matching result dictionaries.
        """
        query_lower = query.lower()
        results = []

        # Search in reverse order (most recent first)
        for chunk in reversed(self._chunks):
            if self._matches(chunk, query_lower):
                results.append(
                    {
                        "summary": chunk.summary,
                        "timestamp": chunk.timestamp,
                        "num_messages": len(chunk.messages),
                    },
                )
                if len(results) >= limit:
                    break

        return results

    async def clear(self) -> None:
        """Clear all offloaded data."""
        self._chunks.clear()

    async def size(self) -> int:
        """Return the number of offloaded chunks.

        Returns:
            `int`:
                The number of offloaded chunks stored.
        """
        return len(self._chunks)

    @staticmethod
    def _matches(chunk: OffloadedChunk, query_lower: str) -> bool:
        """Check if a chunk matches the query via case-insensitive
        substring matching.

        Args:
            chunk (`OffloadedChunk`):
                The chunk to check.
            query_lower (`str`):
                The lowercased query string.

        Returns:
            `bool`:
                True if the query matches the chunk's summary or any
                message content.
        """
        # Check summary
        if query_lower in chunk.summary.lower():
            return True

        # Check message content
        for msg in chunk.messages:
            text = msg.get_text_content()
            if text and query_lower in text.lower():
                return True

        return False
