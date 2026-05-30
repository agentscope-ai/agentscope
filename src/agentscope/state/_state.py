# -*- coding: utf-8 -*-
"""The agent state class."""
import uuid

from pydantic import BaseModel, Field

import aiofiles.os

from ._task import Task
from ..message import TextBlock, DataBlock, Msg
from ..permission import PermissionContext


class ReadCacheEntry(BaseModel):
    """The read file cache."""

    lines: list[str]
    updated_at: float
    bytes: float
    file_path: str
    tool_call_id: str | None = None


class ToolContext(BaseModel):
    """The tool context, e.g. tool cache"""

    max_cache_files: int = Field(default=100, gt=1)
    """The maximum number of cached files."""
    max_cache_bytes: float = Field(default=25000, gt=10000)
    """The maximum size of the accumulated read file cache."""
    read_file_cache: list[ReadCacheEntry] = Field(default_factory=list)
    """The cache for Read/Write/Edit file tools."""

    activated_groups: list[str] = Field(default_factory=list)
    """The names of the activated tool groups, each group contains a set of
    tools."""

    async def get_cache(self, file_path: str) -> ReadCacheEntry | None:
        """Get cached file content if still valid.

        Args:
            file_path: The absolute path of the file.

        Returns:
            The cached entry if valid, otherwise None.
        """

        # Find the cache entry
        for entry in self.read_file_cache:
            if entry.file_path == file_path:
                # Check if cache is still valid
                try:
                    updated_at = await aiofiles.os.path.getmtime(file_path)
                    if updated_at == entry.updated_at:
                        return entry
                    else:
                        # Cache is outdated, remove it
                        self.read_file_cache.remove(entry)
                        return None
                except Exception:
                    # File might not exist anymore
                    self.read_file_cache.remove(entry)
                    return None
        return None

    async def cache_file(
        self,
        file_path: str,
        lines: list[str],
        tool_call_id: str | None = None,
    ) -> None:
        """Cache file content with LRU eviction.

        Args:
            file_path: The absolute path of the file.
            lines: The lines of the file content.
            tool_call_id: The id of the Read tool call that produced the
                cache.
        """
        try:
            updated_at = await aiofiles.os.path.getmtime(file_path)
        except Exception:
            # Cannot get mtime, skip caching
            return

        # Calculate size in KB
        new_entry_bytes = (
            sum(len(line.encode("utf-8")) for line in lines) / 1024
        )

        # Remove existing cache for this file if present
        self.read_file_cache = [
            entry
            for entry in self.read_file_cache
            if entry.file_path != file_path
        ]

        # Evict the oldest entries if exceeding max_cache_files
        while len(self.read_file_cache) >= self.max_cache_files:
            self.read_file_cache.pop(0)

        # Evict the oldest entries if exceeding max_cache_bytes
        current_size = sum(entry.bytes for entry in self.read_file_cache)
        while (
            self.read_file_cache
            and current_size + new_entry_bytes > self.max_cache_bytes
        ):
            removed = self.read_file_cache.pop(0)
            current_size -= removed.bytes

        # Add new entry to the end (most recent)
        self.read_file_cache.append(
            ReadCacheEntry(
                lines=lines,
                updated_at=updated_at,
                bytes=new_entry_bytes,
                file_path=file_path,
                tool_call_id=tool_call_id,
            ),
        )

    def clear_read_cache_for_tool_calls(
        self,
        tool_call_ids: set[str],
        fallback_file_paths: set[str] | None = None,
        reserved_file_paths: set[str] | None = None,
    ) -> None:
        """Clear read caches associated with evicted Read tool calls.

        Args:
            tool_call_ids: The evicted Read tool call ids.
            fallback_file_paths: File paths from evicted Read calls. Used for
                older cache entries that do not have a tool_call_id.
            reserved_file_paths: File paths from Read calls that remain in the
                context. Older path-only caches for these files are kept.
        """
        fallback_file_paths = fallback_file_paths or set()
        reserved_file_paths = reserved_file_paths or set()

        self.read_file_cache = [
            entry
            for entry in self.read_file_cache
            if not (
                (
                    entry.tool_call_id is not None
                    and entry.tool_call_id in tool_call_ids
                )
                or (
                    entry.tool_call_id is None
                    and entry.file_path in fallback_file_paths
                    and entry.file_path not in reserved_file_paths
                )
            )
        ]


class TaskContext(BaseModel):
    """The task context."""

    tasks: list[Task] = Field(default_factory=lambda: [])
    """The task context."""


class AgentState(BaseModel):
    """The agent state that should be saved and loaded from storage."""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The session id of the agent. Normally, each session will maintain one
    independent agent state for each agent."""

    summary: str | list[TextBlock | DataBlock] = ""
    """The compressed summary of the context, which will be prepended to the
    context when feed into the LLM."""
    context: list[Msg] = Field(default_factory=list)
    """The uncompressed conversation context, that will be feed into the LLM"""
    reply_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The id of the current reply, which is also used as the id of the
    final message of the reply."""
    cur_iter: int = 0
    """The current iteration of the agent's reasoning-acting loop."""

    # =================================================================
    # The permission context
    # =================================================================
    permission_context: PermissionContext = Field(
        default_factory=PermissionContext,
    )
    """The permission context that will be passed to the toolkit to determine
    the tool permissions."""

    # =================================================================
    # The tool context
    # =================================================================
    tool_context: ToolContext = Field(default_factory=ToolContext)

    # =================================================================
    # The tasks context
    # =================================================================
    tasks_context: TaskContext = Field(default_factory=TaskContext)
    """The task context that records the agent tasks."""
