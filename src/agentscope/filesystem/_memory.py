# -*- coding: utf-8 -*-
"""In-memory logical filesystem backend for testing and examples."""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Sequence

from ._base import FileSystemBase
from ._errors import ConflictError, NotFoundError
from ._types import EntryMeta, Grant, Path


def _clone_meta(meta: EntryMeta) -> EntryMeta:
    clone: EntryMeta = {"path": meta["path"]}
    if "size" in meta:
        clone["size"] = meta["size"]
    if "updated_at" in meta:
        clone["updated_at"] = meta["updated_at"]
    return clone


class InMemoryFileSystem(FileSystemBase):
    """Simple thread-safe filesystem backed by an in-memory dictionary."""

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[Path, bytes] = {}
        self._meta: dict[Path, EntryMeta] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def _snapshot_impl(self, grants: Sequence[Grant]) -> dict[Path, EntryMeta]:
        with self._lock:
            visible: dict[Path, EntryMeta] = {}
            for path, meta in self._meta.items():
                if not grants:
                    visible[path] = _clone_meta(meta)
                    continue
                if any(path.startswith(grant["prefix"]) for grant in grants):
                    visible[path] = _clone_meta(meta)
            return visible

    def _read_binary_impl(self, path: Path) -> bytes:
        with self._lock:
            try:
                return self._store[path]
            except KeyError as exc:  # pragma: no cover - backend invariant
                raise NotFoundError(path) from exc

    def _read_file_impl(
        self,
        path: Path,
        *,
        index: int | None,
        line: int | None,
    ) -> str:
        text = self._read_binary_impl(path).decode("utf-8")
        if index is None and line is None:
            return text

        lines = text.splitlines()
        start = index or 0
        end = start + line if line is not None else len(lines)
        return "\n".join(lines[start:end])

    def _read_re_impl(
        self,
        path: Path,
        pattern: str,
        overlap: int | None,
    ) -> list[str]:
        text = self._read_binary_impl(path).decode("utf-8")
        compiled = re.compile(pattern, re.MULTILINE)
        if not overlap:
            return [match.group(0) for match in compiled.finditer(text)]

        matches: list[str] = []
        pos = 0
        while pos <= len(text):
            match = compiled.search(text, pos)
            if not match:
                break
            chunk = match.group(0)
            matches.append(chunk)
            match_len = len(chunk)
            advance = max(1, match_len - overlap)
            pos = match.start() + advance
        return matches

    def _write_impl(
        self,
        path: Path,
        data: bytes | str,
        overwrite: bool,
    ) -> EntryMeta:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        with self._lock:
            if not overwrite and path in self._store:
                raise ConflictError(path)
            self._store[path] = payload
            meta: EntryMeta = {
                "path": path,
                "size": len(payload),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._meta[path] = meta
            return _clone_meta(meta)

    def _delete_impl(self, path: Path) -> None:
        with self._lock:
            if path not in self._store:
                raise NotFoundError(path)
            del self._store[path]
            self._meta.pop(path, None)
