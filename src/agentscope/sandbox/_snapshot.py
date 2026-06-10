# -*- coding: utf-8 -*-
"""Sandbox snapshot layer — persists/restores workspace archives.

Ported from Java ``SandboxSnapshot`` / ``SandboxSnapshotSpec`` hierarchy.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO

from .._logging import logger


class SandboxSnapshot(ABC):
    """Strategy for persisting and restoring a sandbox workspace archive."""

    @abstractmethod
    async def persist(self, archive_stream: IO[bytes]) -> None:
        """Store the workspace tar archive."""

    @abstractmethod
    async def restore(self) -> IO[bytes]:
        """Return a readable stream of the stored archive.

        Raises:
            FileNotFoundError: If no snapshot exists.
        """

    @abstractmethod
    async def is_restorable(self) -> bool:
        """Return ``True`` when a stored snapshot is available."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Snapshot identifier."""

    @property
    @abstractmethod
    def type(self) -> str:
        """Discriminator for polymorphic deserialization."""

    def is_persistence_enabled(self) -> bool:
        """Return ``False`` to skip the expensive archiving step.

        Only :class:`NoopSandboxSnapshot` overrides this to ``False``.
        """
        return True


class SandboxSnapshotSpec(ABC):
    """Factory that mints a :class:`SandboxSnapshot` per session ID."""

    @abstractmethod
    def build(self, snapshot_id: str) -> SandboxSnapshot:
        """Create a snapshot instance for the given identifier."""


class NoopSandboxSnapshot(SandboxSnapshot):
    """Null-object snapshot that discards all data.

    When used, workspace state is **not** preserved between session stops;
    every start begins fresh from the full manifest.
    """

    def __init__(self, snapshot_id: str = "") -> None:
        self._id = snapshot_id or uuid.uuid4().hex

    @property
    def id(self) -> str:
        return self._id

    @property
    def type(self) -> str:
        return "noop"

    def is_persistence_enabled(self) -> bool:
        return False

    async def persist(self, archive_stream: IO[bytes]) -> None:
        # Drain the stream so the caller can close it cleanly.
        archive_stream.read()

    async def restore(self) -> IO[bytes]:
        raise FileNotFoundError("NoopSandboxSnapshot has no data")

    async def is_restorable(self) -> bool:
        return False


class NoopSnapshotSpec(SandboxSnapshotSpec):
    """Spec that manufactures :class:`NoopSandboxSnapshot` instances."""

    def build(self, snapshot_id: str) -> SandboxSnapshot:
        return NoopSandboxSnapshot(snapshot_id)


class LocalSandboxSnapshot(SandboxSnapshot):
    """Persists workspace archives as local tar files.

    Uses **atomic writes**: streams to a hidden temp file then renames.
    Validates snapshot IDs against path-traversal characters.
    """

    _FORBIDDEN = set("/\\..\x00")

    def __init__(self, base_path: str | Path, snapshot_id: str) -> None:
        self._base = Path(base_path)
        self._id = self._validate_id(snapshot_id)
        self._path = self._base / f"{self._id}.tar"

    @property
    def id(self) -> str:
        return self._id

    @property
    def type(self) -> str:
        return "local"

    @property
    def base_path(self) -> Path:
        return self._base

    def _validate_id(self, snapshot_id: str) -> str:
        if any(c in self._FORBIDDEN for c in snapshot_id):
            raise ValueError(
                f"Snapshot ID contains forbidden characters: {snapshot_id!r}",
            )
        return snapshot_id

    async def persist(self, archive_stream: IO[bytes]) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        tmp = self._base / f".{self._id}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp, "wb") as f:
                while True:
                    chunk = archive_stream.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            # Atomic move on POSIX; best-effort on Windows.
            os.replace(str(tmp), str(self._path))
            logger.debug(
                "LocalSandboxSnapshot: persisted %s → %s",
                self._id,
                self._path,
            )
        except Exception:
            # Clean up temp file on failure.
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    async def restore(self) -> IO[bytes]:
        if not self._path.exists():
            raise FileNotFoundError(f"Snapshot not found: {self._path}")
        return open(self._path, "rb")

    async def is_restorable(self) -> bool:
        return self._path.exists() and self._path.stat().st_size > 0


class LocalSnapshotSpec(SandboxSnapshotSpec):
    """Spec that builds :class:`LocalSandboxSnapshot` instances."""

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)

    def build(self, snapshot_id: str) -> SandboxSnapshot:
        return LocalSandboxSnapshot(self._base, snapshot_id)
