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
from ._disk import DiskFileSystem
from ._service import FileDomainService, DomainPolicy
from ._tools import (
    read_text_file,
    read_multiple_files,
    list_directory,
    list_directory_with_sizes,
    search_files,
    get_file_info,
    list_allowed_directories,
    write_file,
    edit_file,
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
    "DiskFileSystem",
    "FileDomainService",
    "DomainPolicy",
    # tools (register with preset_kwargs={"service": svc})
    "read_text_file",
    "read_multiple_files",
    "list_directory",
    "list_directory_with_sizes",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
    "write_file",
    "edit_file",
]
