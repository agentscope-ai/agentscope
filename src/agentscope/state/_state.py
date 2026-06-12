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

    async def cache_file(self, file_path: str, lines: list[str]) -> None:
        """Cache file content with LRU eviction.

        Args:
            file_path: The absolute path of the file.
            lines: The lines of the file content.
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
            ),
        )

    async def clean_file_cache(
        self,
        reserved_file_paths: set[str] | None = None,
    ) -> None:
        """Drop read caches whose paths are not in ``reserved_file_paths``.

        Args:
            reserved_file_paths: File paths from Read calls that remain in the
                context. Caches for these files are kept; all others are
                evicted.
        """
        reserved_file_paths = reserved_file_paths or set()

        self.read_file_cache = [
            entry
            for entry in self.read_file_cache
            if entry.file_path in reserved_file_paths
        ]


class MemoryEntry(BaseModel):
    """A single memory entry stored by the agent."""

    key: str
    """The unique key for this memory entry."""
    value: str
    """The content of the memory entry."""
    created_at: float
    """The timestamp when this memory was created."""


class MemoryContext(BaseModel):
    """The memory context for explicit agent memory management.

    Allows agents to store, retrieve, update, and delete named memories
    that persist within the session. This complements the automatic
    context compression by giving agents explicit control over what
    information to remember."""

    max_entries: int = Field(default=50, gt=0)
    """The maximum number of memory entries to retain."""
    entries: dict[str, MemoryEntry] = Field(default_factory=dict)
    """The stored memory entries, keyed by name."""

    def set(self, key: str, value: str, timestamp: float) -> None:
        """Store or update a memory entry.

        Args:
            key: The unique key for the memory.
            value: The content to store.
            timestamp: The creation/update timestamp.
        """
        # Evict oldest entry if at capacity and adding a new key
        if key not in self.entries and len(self.entries) >= self.max_entries:
            oldest = min(
                self.entries.values(),
                key=lambda e: e.created_at,
            )
            del self.entries[oldest.key]
        self.entries[key] = MemoryEntry(
            key=key,
            value=value,
            created_at=timestamp,
        )

    def delete(self, key: str) -> bool:
        """Delete a memory entry.

        Args:
            key: The key of the memory to delete.

        Returns:
            True if the entry was deleted, False if it didn't exist.
        """
        if key in self.entries:
            del self.entries[key]
            return True
        return False


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

    # =================================================================
    # The memory context
    # =================================================================
    memory_context: MemoryContext = Field(default_factory=MemoryContext)
    """The memory context for explicit agent memory storage and
    retrieval."""
