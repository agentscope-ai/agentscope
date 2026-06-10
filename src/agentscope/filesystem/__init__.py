# -*- coding: utf-8 -*-
"""Agent-facing filesystem abstractions and implementations."""
from __future__ import annotations

from ._models import (
    FileInfo,
    GrepMatch,
    LsResult,
    ReadResult,
    WriteResult,
    EditResult,
    GrepResult,
    GlobResult,
    FileUploadResponse,
    FileDownloadResponse,
)
from ._abstract import AbstractFilesystem
from ._local import LocalFilesystem, LocalFsMode
from ._remote import RemoteFilesystem
from ._composite import CompositeFilesystem
from ._overlay import OverlayFilesystem
from ._base_store import BaseStore, StoreKey, StoreValue
from ._in_memory_store import InMemoryStore
from ._namespace_factory import NamespaceFactory
from ._workspace_index import WorkspaceIndex

__all__ = [
    "AbstractFilesystem",
    "LocalFilesystem",
    "LocalFsMode",
    "RemoteFilesystem",
    "CompositeFilesystem",
    "OverlayFilesystem",
    "BaseStore",
    "StoreKey",
    "StoreValue",
    "InMemoryStore",
    "NamespaceFactory",
    "WorkspaceIndex",
    # Result models
    "FileInfo",
    "GrepMatch",
    "LsResult",
    "ReadResult",
    "WriteResult",
    "EditResult",
    "GrepResult",
    "GlobResult",
    "FileUploadResponse",
    "FileDownloadResponse",
]
