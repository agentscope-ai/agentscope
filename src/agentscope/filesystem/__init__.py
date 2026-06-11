# -*- coding: utf-8 -*-
"""Agent-facing filesystem abstractions and implementations.

This module provides a family of filesystem implementations that let agents
operate on files in a variety of environments — local disk, remote sandbox,
or layered composites.

**Core abstractions**

- :class:`AbstractFilesystem` — The base interface (read, write, ls, grep, glob).
- :class:`BaseStore` / :class:`StoreKey` / :class:`StoreValue` — Namespace-scoped
  key-value storage used by :class:`RemoteFilesystem`.

**Implementations**

- :class:`LocalFilesystem` — Direct local-disk access with three security modes
  (``STRICT``, ``WORKSPACE``, ``UNRESTRICTED``).
- :class:`RemoteFilesystem` — KV-store-backed filesystem with optimistic
  concurrency control. Suitable for stateless workers talking to a shared
  storage backend (e.g. Redis, S3).
- :class:`CompositeFilesystem` — Prefix-based router that shards paths across
  multiple backend filesystems.
- :class:`OverlayFilesystem` — Copy-on-write layering where an upper (writable)
  layer masks a lower (read-only) layer.

**Supporting types**

- :class:`NamespaceFactory` — Derives namespace strings from ``user_id`` /
  ``session_id`` pairs.
- :class:`WorkspaceIndex` — SQLite-backed index for fast directory listings
  and search.
- :class:`InMemoryStore` — A trivial :class:`BaseStore` for tests and
  single-process use.
"""
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
