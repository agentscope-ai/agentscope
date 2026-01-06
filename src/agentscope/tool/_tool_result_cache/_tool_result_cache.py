# -*- coding: utf-8 -*-
""""""
from abc import abstractmethod

from litellm.types.llms.bedrock import ToolResultBlock


class ToolResultCacheBase:
    def __init__(
        self,
        max_cache_size: int | None = None,
    ) -> None:
        """Initialize the tool result cache object.

        Args:
            max_cache_size (`int`, *optional*, defaults to `None`):
                The maximum number of tool results to store in the cache. If
                `None`, the cache size is unlimited.
        """
        self.max_cache_size = max_cache_size

    @abstractmethod
    def retrieve(self, tool_result_id: str, chunk_id: str) -> dict | None:
        """Retrieve a tool result from the cache by the tool result ID and chunk ID.

        Args:
            tool_result_id (`str`):
                The unique identifier of the tool result.
            chunk_id (`str`):
                The unique identifier of the chunk within the tool result.
        """

    @abstractmethod
    def cache_tool_result(self, tool_result: ToolResultBlock) -> dict | None:
        """Cache a tool result and wait for future retrieval."""

    @abstractmethod
    def dumps(self) -> dict:
        """Convert the current cache into JSON data format."""

    @abstractmethod
    def size(self) -> int:
        """Get the current size of the cache."""

    def __len__(self) -> int:
        """Get the current size of the cache."""
        return self.size()
