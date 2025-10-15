# -*- coding: utf-8 -*-
"""Exception hierarchy for the logical filesystem module."""
from __future__ import annotations

from typing import Any

from ._types import Operation, Path


class FileSystemError(RuntimeError):
    """Base error for logical filesystem operations."""

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


class InvalidPathError(FileSystemError):
    """Raised when a logical path violates formatting or prefix rules."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Invalid logical path: {path}", path=path)


class AccessDeniedError(FileSystemError):
    """Raised when the caller lacks permission for the requested operation."""

    def __init__(self, path: Path, operation: Operation) -> None:
        super().__init__(
            f"Access denied for operation '{operation}' on path {path}",
            path=path,
        )
        self.operation = operation


class NotFoundError(FileSystemError):
    """Raised when a logical path does not exist in the filesystem view."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Path not found: {path}", path=path)


class ConflictError(FileSystemError):
    """Raised when a write/delete operation collides with existing state."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Conflict detected for path: {path}", path=path)


class InvalidArgumentError(FileSystemError):
    """Raised when supplied parameters violate operation invariants."""

    def __init__(self, argument: str, *, value: Any | None = None) -> None:
        message = f"Invalid argument '{argument}'"
        if value is not None:
            message += f" (value={value!r})"
        super().__init__(message)
        self.argument = argument
        self.value = value
