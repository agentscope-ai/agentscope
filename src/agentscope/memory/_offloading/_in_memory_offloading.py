# -*- coding: utf-8 -*-
"""In-memory implementation of memory offloading.

This module provides a default, dependency-free implementation of
`MemoryOffloadingBase` that stores offloaded memory chunks in a
Python list and searches via substring matching.

Suitable for single-session use, prototyping, and testing. For
production use with large memory volumes, consider implementing a
backend based on vector databases or embedding-based search.
"""
import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from ...message import Msg, TextBlock
from ...tool import ToolResponse
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


class InMemoryMemoryOffloading(MemoryOffloadingBase):
    """Default in-memory implementation of memory offloading.

    Stores offloaded chunks in a Python list and performs substring-based
    search across summaries and message content. No external dependencies
    are required.

    Example:
        .. code-block:: python

            from agentscope.memory import InMemoryMemoryOffloading

            offloading = InMemoryMemoryOffloading()

            # Store compressed messages
            await offloading.store(
                msgs=[msg1, msg2, msg3],
                summary="User asked about Python async patterns",
            )

            # Get tools to register with agent
            tools = offloading.list_tools()
    """

    def __init__(self) -> None:
        """Initialize the in-memory memory offloading."""
        super().__init__()
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

    async def search_memory(
        self,
        query: str,
        limit: int = 5,
    ) -> ToolResponse:
        """Search your compressed conversation history for relevant
        information.

        Use this tool when you need to recall details from earlier in
        the conversation that may have been compressed. This searches
        through summaries and original message content of previously
        compressed memory chunks.

        Args:
            query (`str`):
                The search query. Use specific keywords related to
                the information you're looking for.
            limit (`int`, optional):
                Maximum number of results to return. Defaults to 5.

        Returns:
            `ToolResponse`:
                The search results from offloaded memory.
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

        if not results:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"No offloaded memories found matching "
                            f"'{query}'."
                        ),
                    ),
                ],
            )

        # Format results
        formatted_parts = []
        for i, result in enumerate(results, 1):
            formatted_parts.append(
                f"--- Memory Chunk {i} "
                f"(offloaded at {result['timestamp']}, "
                f"{result['num_messages']} original messages) ---\n"
                f"{result['summary']}",
            )

        combined_text = "\n\n".join(formatted_parts)

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=combined_text,
                ),
            ],
        )

    def list_tools(
        self,
    ) -> list[Callable[..., Coroutine[Any, Any, ToolResponse]]]:
        """List tool functions provided to the agent.

        Returns:
            `list[Callable[..., Coroutine[Any, Any, ToolResponse]]]`:
                A list containing the search_memory tool.
        """
        return [self.search_memory]

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

    def state_dict(self) -> dict:
        """Get the state dictionary for serialization."""
        return {
            **super().state_dict(),
            "_chunks": [
                {
                    "summary": chunk.summary,
                    "messages": [msg.to_dict() for msg in chunk.messages],
                    "timestamp": chunk.timestamp,
                    "keywords": chunk.keywords,
                }
                for chunk in self._chunks
            ],
        }

    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        """Load the state dictionary for deserialization."""
        if strict and "_chunks" not in state_dict:
            raise KeyError(
                "The state_dict does not contain '_chunks' key "
                "required for InMemoryMemoryOffloading.",
            )

        self._chunks = []
        for item in state_dict.get("_chunks", []):
            chunk = OffloadedChunk(
                summary=item["summary"],
                messages=[Msg.from_dict(m) for m in item["messages"]],
                timestamp=item["timestamp"],
                keywords=item.get("keywords", []),
            )
            self._chunks.append(chunk)

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
        return any(
            (text := msg.get_text_content()) and query_lower in text.lower()
            for msg in chunk.messages
        )
