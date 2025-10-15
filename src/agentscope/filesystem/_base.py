# -*- coding: utf-8 -*-
"""Abstract filesystem base class providing hook surface for backends."""
from __future__ import annotations

from typing import Sequence

from ..module import StateModule
from ._types import EntryMeta, Grant, Path

if False:  # pragma: no cover
    from ._handle import FsHandle


class FileSystemBase(StateModule):
    """Abstract base class for logical filesystem backends."""

    def create_handle(self, grants: Sequence[Grant]) -> "FsHandle":
        """Create a handle enforcing the provided grants."""

        from ._handle import FsHandle  # Local import to avoid circular deps

        return FsHandle(self, list(grants))

    # -- Backend hooks -------------------------------------------------
    def _snapshot_impl(self, grants: Sequence[Grant]) -> dict[Path, EntryMeta]:
        """Return the visible entry metadata keyed by logical path."""

        raise NotImplementedError

    def _read_binary_impl(self, path: Path) -> bytes:
        """Read raw bytes from ``path``."""

        raise NotImplementedError

    def _read_file_impl(
        self,
        path: Path,
        *,
        index: int | None,
        line: int | None,
    ) -> str:
        """Read textual content from ``path`` with optional slicing."""

        raise NotImplementedError

    def _read_re_impl(
        self,
        path: Path,
        pattern: str,
        overlap: int | None,
    ) -> list[str]:
        """Read portions of ``path`` matching a regex pattern."""

        raise NotImplementedError

    def _write_impl(
        self,
        path: Path,
        data: bytes | str,
        overwrite: bool,
    ) -> EntryMeta:
        """Write ``data`` to ``path`` returning resulting metadata."""

        raise NotImplementedError

    def _delete_impl(self, path: Path) -> None:
        """Delete ``path`` from the logical filesystem."""

        raise NotImplementedError
