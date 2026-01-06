# -*- coding: utf-8 -*-
""""""
import json
import os
from datetime import datetime

from litellm.types.llms.bedrock import ToolResultBlock

from ._tool_result_cache import ToolResultCacheBase
from .. import ToolResponse


class FileSystemToolResultCache(ToolResultCacheBase):
    """A file system-based implementation of the ToolResultCacheBase."""

    def __init__(
        self,
        cache_dir: str,
        max_cache_size: int | None = None,
    ) -> None:
        """Initialize the file system tool result cache object.

        Args:
            cache_dir (`str`):
                The directory where the cache files will be stored.
            max_cache_size (`int`, *optional*, defaults to `None`):
                The maximum number of tool results to store in the cache. If
                `None`, the cache size is unlimited.
        """
        super().__init__()

        if os.path.exists(cache_dir):
            if not os.path.isdir(cache_dir):
                raise ValueError(
                    f"The cache_dir '{cache_dir}' exists and is not a "
                    f"directory.",
                )
        else:
            os.makedirs(cache_dir, exist_ok=True)

        self.cache_dir = cache_dir

    def retrieve(self, tool_result_id: str, chunk_index: int) -> ToolResponse:
        """Retrieve a tool result from the cache by the tool result ID and chunk ID.

        Args:
            tool_result_id (`str`):
                The unique identifier of the tool result.
            chunk_index (`int`):
                The index of the chunk within the tool result, starting from 0.
        """
        filename = self._create_cache_file_name(tool_result_id)
        filepath = os.path.join(self.cache_dir, filename)
        if not os.path.exists(filepath):
            return None

    def cache_tool_result(
        self,
        tool_result_id: str,
        tool_result: ToolResultBlock,
    ) -> dict | None:
        """Cache a tool result and wait for future retrieval.

        Args:
            tool_result_id (`str`):
                The unique identifier of the tool result.
            tool_result (`ToolResultBlock`):
                The tool result to be cached.
        """
        filename = self._create_cache_file_name(tool_result_id)
        filepath = os.path.join(self.cache_dir, filename)

        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(tool_result, file, ensure_ascii=False, indent=4)

    @staticmethod
    def _create_cache_file_name(tool_result_id: str) -> str:
        """Get the cache file name for a given tool result ID."""
        return (
            datetime.now().strftime("%Y%m%d-%H%M%S__")
            + tool_result_id
            + ".cache"
        )

    def tool_result_exists(self, tool_result_id: str) -> bool:
        """"""
        for filename in os.listdir(self.cache_dir):
            if filename.endswith(f"__{tool_result_id}.cache"):
                return True
        return False

    def dumps(self) -> dict:
        pass

    def size(self) -> int:
        pass
