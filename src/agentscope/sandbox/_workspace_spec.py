# -*- coding: utf-8 -*-
"""Declarative workspace layout — ported from Java WorkspaceSpec/WorkspaceEntry.

A :class:`WorkspaceSpec` describes the desired filesystem layout of a sandbox
workspace. It is materialised by :class:`WorkspaceSpecApplier` before the
sandbox starts, or replayed from a snapshot on resume.
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkspaceSpec:
    """Declarative description of the desired workspace filesystem.

    Attributes:
        root: Sandbox-side root path (default ``"/workspace"``).
        entries: Mapping from sandbox-relative path to a :class:`WorkspaceEntry`
            describing what should exist at that path.
        environment: Extra environment variables set inside the sandbox.
    """

    root: str = "/workspace"
    entries: dict[str, "WorkspaceEntry"] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)

    def copy(self) -> "WorkspaceSpec":
        """Return a shallow copy."""
        return WorkspaceSpec(
            root=self.root,
            entries=dict(self.entries),
            environment=dict(self.environment),
        )


class WorkspaceEntry(ABC):
    """Abstract base for a single item in a :class:`WorkspaceSpec`.

    Attributes:
        ephemeral: When ``True``, the entry is re-applied on every sandbox
            start even when resuming from a snapshot. Useful for dynamic
            configuration that must not be snapshotted.
    """

    ephemeral: bool = False

    def __init__(self, ephemeral: bool = False) -> None:
        self.ephemeral = ephemeral


@dataclass
class FileEntry(WorkspaceEntry):
    """Create a file with inline text content."""

    content: str = ""
    encoding: str = "utf-8"

    def __init__(
        self,
        content: str = "",
        encoding: str = "utf-8",
        ephemeral: bool = False,
    ) -> None:
        super().__init__(ephemeral=ephemeral)
        self.content = content
        self.encoding = encoding


@dataclass
class DirEntry(WorkspaceEntry):
    """Create a directory with optional nested children."""

    children: dict[str, WorkspaceEntry] = field(default_factory=dict)

    def __init__(
        self,
        children: dict[str, WorkspaceEntry] | None = None,
        ephemeral: bool = False,
    ) -> None:
        super().__init__(ephemeral=ephemeral)
        self.children = dict(children or {})

    def child(self, name: str, entry: WorkspaceEntry) -> "DirEntry":
        """Fluent builder — add a child and return self."""
        self.children[name] = entry
        return self


@dataclass
class LocalFileEntry(WorkspaceEntry):
    """Copy a single file from the host into the sandbox workspace."""

    source_path: str = ""

    def __init__(
        self,
        source_path: str = "",
        ephemeral: bool = False,
    ) -> None:
        super().__init__(ephemeral=ephemeral)
        self.source_path = source_path


@dataclass
class LocalDirEntry(WorkspaceEntry):
    """Recursively copy a directory from the host into the sandbox workspace."""

    source_path: str = ""

    def __init__(
        self,
        source_path: str = "",
        ephemeral: bool = False,
    ) -> None:
        super().__init__(ephemeral=ephemeral)
        self.source_path = source_path


@dataclass
class BindMountEntry(WorkspaceEntry):
    """Declare a host path to be bind-mounted into the sandbox.

    Backend-specific handling:
    - Docker → ``-v host:container``
    - Kubernetes → HostPath volume + volumeMount
    - Cloud sandboxes (E2B) → typically ignored at runtime
    """

    host_path: str = ""
    read_only: bool = False

    def __init__(
        self,
        host_path: str = "",
        read_only: bool = False,
        ephemeral: bool = False,
    ) -> None:
        super().__init__(ephemeral=ephemeral)
        self.host_path = host_path
        self.read_only = read_only


@dataclass
class GitRepoEntry(WorkspaceEntry):
    """Clone a Git repository into the sandbox workspace."""

    url: str = ""
    ref: str = ""

    def __init__(
        self,
        url: str = "",
        ref: str = "",
        ephemeral: bool = False,
    ) -> None:
        super().__init__(ephemeral=ephemeral)
        self.url = url
        self.ref = ref


@dataclass
class WorkspaceProjectionEntry(WorkspaceEntry):
    """Project selected files/directories from the host workspace into the sandbox.

    This is sandbox-specific and is **ignored** by :class:`WorkspaceSpecApplier`'s
    regular materialisation; instead it is applied inside ``Sandbox.start()``
    via archive hydration.
    """

    source_root: str = ""
    include_roots: list[str] = field(default_factory=list)

    def __init__(
        self,
        source_root: str = "",
        include_roots: list[str] | None = None,
        ephemeral: bool = False,
    ) -> None:
        super().__init__(ephemeral=ephemeral)
        self.source_root = source_root
        self.include_roots = list(include_roots or [])
