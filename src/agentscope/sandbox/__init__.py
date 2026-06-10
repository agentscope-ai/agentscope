# -*- coding: utf-8 -*-
"""Sandbox system — pool management, leasing, isolation keys, and lifecycle."""

from ._types import (
    IsolationScope,
    SandboxAcquireResult,
    SandboxContext,
    SandboxExecutionGuard,
    SandboxIsolationKey,
    SandboxLease,
    SandboxState,
    noop_execution_guard,
)
from ._sandbox import Sandbox
from ._client import SandboxClient
from ._state_store import (
    InMemorySandboxStateStore,
    SandboxStateStore,
    StorageBackedSandboxStateStore,
)
from ._manager import SandboxManager
from ._workspace_adapter import WorkspaceSandbox, WorkspaceSandboxClient
from ._workspace_spec import (
    WorkspaceSpec,
    WorkspaceEntry,
    FileEntry,
    DirEntry,
    LocalFileEntry,
    LocalDirEntry,
    BindMountEntry,
    GitRepoEntry,
    WorkspaceProjectionEntry,
)
from ._workspace_spec_applier import WorkspaceSpecApplier
from ._workspace_archive_extractor import (
    WorkspaceArchiveExtractor,
    ArchiveExtractError,
)
from ._workspace_projection_applier import (
    WorkspaceProjectionApplier,
    ProjectionPayload,
)
from ._snapshot import (
    SandboxSnapshot,
    SandboxSnapshotSpec,
    NoopSandboxSnapshot,
    NoopSnapshotSpec,
    LocalSandboxSnapshot,
    LocalSnapshotSpec,
)

__all__ = [
    "IsolationScope",
    "Sandbox",
    "SandboxAcquireResult",
    "SandboxClient",
    "SandboxContext",
    "SandboxExecutionGuard",
    "SandboxIsolationKey",
    "SandboxLease",
    "SandboxManager",
    "SandboxState",
    "SandboxStateStore",
    "InMemorySandboxStateStore",
    "StorageBackedSandboxStateStore",
    "WorkspaceSandbox",
    "WorkspaceSandboxClient",
    "noop_execution_guard",
    # Workspace spec
    "WorkspaceSpec",
    "WorkspaceEntry",
    "FileEntry",
    "DirEntry",
    "LocalFileEntry",
    "LocalDirEntry",
    "BindMountEntry",
    "GitRepoEntry",
    "WorkspaceProjectionEntry",
    "WorkspaceSpecApplier",
    "WorkspaceArchiveExtractor",
    "ArchiveExtractError",
    "WorkspaceProjectionApplier",
    "ProjectionPayload",
    # Snapshot
    "SandboxSnapshot",
    "SandboxSnapshotSpec",
    "NoopSandboxSnapshot",
    "NoopSnapshotSpec",
    "LocalSandboxSnapshot",
    "LocalSnapshotSpec",
]
