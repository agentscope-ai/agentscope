# -*- coding: utf-8 -*-
"""Abstract filesystem interface for agent-facing file operations."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ._models import (
    LsResult,
    ReadResult,
    WriteResult,
    EditResult,
    GrepResult,
    GlobResult,
)


class AbstractFilesystem(ABC):
    """Core contract for all agent-facing filesystem implementations.

    Every operation receives a *runtime_context* dict so implementations can
    scope work to the current session, user, or sandbox.
    """

    @abstractmethod
    async def ls(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> LsResult:
        """List directory contents."""

    @abstractmethod
    async def read(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> ReadResult:
        """Read file contents.

        Args:
            offset: Line offset (0-based). 0 means start from the first line.
            limit: Maximum number of lines to read. 0 means no limit.
        """

    @abstractmethod
    async def write(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        content: str,
    ) -> WriteResult:
        """Write content to a file (overwrites existing)."""

    @abstractmethod
    async def edit(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Replace *old_string* with *new_string* in a file."""

    @abstractmethod
    async def grep(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
        glob: str = "",
    ) -> GrepResult:
        """Search for *pattern* under *path*."""

    @abstractmethod
    async def glob(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
    ) -> GlobResult:
        """Find files matching *pattern* under *path*."""

    @abstractmethod
    async def delete(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> WriteResult:
        """Delete a file or directory. Idempotent: missing paths are OK."""

    @abstractmethod
    async def move(
        self,
        runtime_context: dict[str, Any],
        from_path: str,
        to_path: str,
    ) -> WriteResult:
        """Move or rename a file/directory."""

    @abstractmethod
    async def exists(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> bool:
        """Return ``True`` if *path* exists."""

    @staticmethod
    def validate_path(path: str) -> None:
        """Reject ``null``, blank, and ``..`` traversal strings.

        Raises:
            ValueError: If the path is invalid.
        """
        if not path:
            raise ValueError("Path must not be empty")
        if ".." in path.split("/"):
            raise ValueError(f"Path contains '..' traversal: {path!r}")
