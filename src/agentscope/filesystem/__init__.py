# -*- coding: utf-8 -*-
"""Logical filesystem module exports."""
from ._types import Path, Operation, Grant, EntryMeta
from ._errors import (
    FileSystemError,
    InvalidPathError,
    AccessDeniedError,
    NotFoundError,
    ConflictError,
    InvalidArgumentError,
)
from ._base import FileSystemBase
from ._handle import FsHandle, validate_path
from ._memory import InMemoryFileSystem
from ._builtin import (
    BuiltinFileSystem,
    builtin_grant,
    builtin_grants,
    INTERNAL_PREFIX,
    USERINPUT_PREFIX,
    WORKSPACE_PREFIX,
)

__all__ = [
    "Path",
    "Operation",
    "Grant",
    "EntryMeta",
    "FileSystemError",
    "InvalidPathError",
    "AccessDeniedError",
    "NotFoundError",
    "ConflictError",
    "InvalidArgumentError",
    "FileSystemBase",
    "FsHandle",
    "validate_path",
    "InMemoryFileSystem",
    "BuiltinFileSystem",
    "builtin_grant",
    "builtin_grants",
    "INTERNAL_PREFIX",
    "USERINPUT_PREFIX",
    "WORKSPACE_PREFIX",
]
