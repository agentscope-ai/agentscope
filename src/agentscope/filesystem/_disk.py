# -*- coding: utf-8 -*-
"""Disk-backed logical filesystem implementing the three-namespace reference.

Default root: ./output/{mmddHHMM}/ created on initialization when root_dir
is not provided. The backend maps logical prefixes to OS directories and
implements FileSystemBase hooks without exposing OS paths to callers.
Also exposes a tools listing for convenient Toolkit registration, but does
not return a Toolkit nor capture any handle/service.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Sequence, Tuple

from ._base import FileSystemBase
from ._errors import ConflictError, NotFoundError
from ._types import EntryMeta, Grant, Path
from ._handle import validate_path
from ._builtin import (
    INTERNAL_PREFIX,
    USERINPUT_PREFIX,
    WORKSPACE_PREFIX,
)
"""Do not import _tools at module import time to avoid heavy package chains.
Tool functions are imported lazily inside supported_tools().
"""


_RE_PATH_SEP = re.compile(r"\\+")


def _norm_join(base_dir: str, *parts: str) -> str:
    return os.path.abspath(os.path.join(base_dir, *parts))


class DiskFileSystem(FileSystemBase):
    """Disk-backed filesystem that adheres to the logical FS SOP."""

    def __init__(
        self,
        *,
        root_dir: str | None = None,
        internal_dir: str | None = None,
        userinput_dir: str | None = None,
        workspace_dir: str | None = None,
    ) -> None:
        super().__init__()
        if root_dir is None:
            # Include seconds to avoid reusing the same minute bucket
            stamp = datetime.now().strftime("%m%d%H%M%S")
            base = os.path.abspath(os.path.join(os.getcwd(), "output", stamp))
        else:
            base = os.path.abspath(root_dir)

        self._root_dir = base
        self._internal_dir = os.path.abspath(
            internal_dir or os.path.join(base, "internal"),
        )
        self._userinput_dir = os.path.abspath(
            userinput_dir or os.path.join(base, "userinput"),
        )
        self._workspace_dir = os.path.abspath(
            workspace_dir or os.path.join(base, "workspace"),
        )

        # Ensure dirs exist
        os.makedirs(self._internal_dir, exist_ok=True)
        os.makedirs(self._userinput_dir, exist_ok=True)
        os.makedirs(self._workspace_dir, exist_ok=True)

        # Create a minimal creation marker under internal to satisfy
        # "initialization creates a file" requirement.
        try:
            marker = os.path.join(self._internal_dir, ".created")
            if not os.path.exists(marker):
                with open(marker, "w", encoding="utf-8") as f:
                    f.write(datetime.now(tz=timezone.utc).isoformat())
        except Exception:
            pass

    # ----------------------------- Helpers -----------------------------
    def _split_namespace(self, path: Path) -> Tuple[str, str]:
        validate_path(path)
        if path.startswith(INTERNAL_PREFIX):
            return INTERNAL_PREFIX, path[len(INTERNAL_PREFIX) :]
        if path.startswith(USERINPUT_PREFIX):
            return USERINPUT_PREFIX, path[len(USERINPUT_PREFIX) :]
        if path.startswith(WORKSPACE_PREFIX):
            return WORKSPACE_PREFIX, path[len(WORKSPACE_PREFIX) :]
        raise NotFoundError(path)

    def _ns_root(self, prefix: str) -> str:
        if prefix == INTERNAL_PREFIX:
            return self._internal_dir
        if prefix == USERINPUT_PREFIX:
            return self._userinput_dir
        if prefix == WORKSPACE_PREFIX:
            return self._workspace_dir
        return self._workspace_dir

    def _to_os_path(self, path: Path) -> str:
        prefix, rel = self._split_namespace(path)
        rel_norm = _RE_PATH_SEP.sub("/", rel).lstrip("/")
        pieces = rel_norm.split("/") if rel_norm else []
        return _norm_join(
            self._ns_root(prefix),
            *pieces,
        )

    def _iter_visible_under(
        self,
        prefix: str,
        base_dir: str,
    ) -> dict[Path, EntryMeta]:
        view: dict[Path, EntryMeta] = {}
        for root, _dirs, files in os.walk(base_dir):
            for fname in files:
                abs_path = os.path.join(root, fname)
                rel = os.path.relpath(abs_path, base_dir)
                rel = rel.replace(os.sep, "/")
                logical = prefix + rel if rel else prefix
                stat = os.stat(abs_path)
                view[logical] = {
                    "path": logical,
                    "size": int(stat.st_size),
                    "updated_at": datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=timezone.utc,
                    ).isoformat(),
                }
        return view

    # --------------------------- Backend hooks -------------------------
    def _snapshot_impl(self, grants: Sequence[Grant]) -> dict[Path, EntryMeta]:
        visible: dict[Path, EntryMeta] = {}
        prefixes = {g["prefix"] for g in grants} if grants else {
            INTERNAL_PREFIX,
            USERINPUT_PREFIX,
            WORKSPACE_PREFIX,
        }
        if INTERNAL_PREFIX in prefixes:
            visible.update(
                self._iter_visible_under(INTERNAL_PREFIX, self._internal_dir),
            )
        if USERINPUT_PREFIX in prefixes:
            visible.update(
                self._iter_visible_under(
                    USERINPUT_PREFIX,
                    self._userinput_dir,
                ),
            )
        if WORKSPACE_PREFIX in prefixes:
            visible.update(
                self._iter_visible_under(
                    WORKSPACE_PREFIX,
                    self._workspace_dir,
                ),
            )
        return visible

    def _read_binary_impl(self, path: Path) -> bytes:
        os_path = self._to_os_path(path)
        try:
            with open(os_path, "rb") as f:
                return f.read()
        except FileNotFoundError as exc:  # pragma: no cover
            raise NotFoundError(path) from exc

    def _read_file_impl(
        self,
        path: Path,
        *,
        index: int | None,
        line: int | None,
    ) -> str:
        data = self._read_binary_impl(path).decode("utf-8")
        if index is None and line is None:
            return data
        lines = data.splitlines()
        start = index or 0
        end = start + line if line is not None else len(lines)
        return "\n".join(lines[start:end])

    def _read_re_impl(
        self,
        path: Path,
        pattern: str,
        overlap: int | None,
    ) -> list[str]:
        import re as _re

        text = self._read_binary_impl(path).decode("utf-8")
        compiled = _re.compile(pattern, _re.MULTILINE)
        if not overlap:
            return [m.group(0) for m in compiled.finditer(text)]
        matches: list[str] = []
        pos = 0
        while pos <= len(text):
            match = compiled.search(text, pos)
            if not match:
                break
            chunk = match.group(0)
            matches.append(chunk)
            advance = max(1, len(chunk) - int(overlap))
            pos = match.start() + advance
        return matches

    def _write_impl(
        self,
        path: Path,
        data: bytes | str,
        overwrite: bool,
    ) -> EntryMeta:
        os_path = self._to_os_path(path)
        if not overwrite and os.path.exists(os_path):
            raise ConflictError(path)
        os.makedirs(os.path.dirname(os_path), exist_ok=True)
        payload = data.encode("utf-8") if isinstance(data, str) else data
        with open(os_path, "wb") as f:
            f.write(payload)
        stat = os.stat(os_path)
        meta: EntryMeta = {
            "path": path,
            "size": int(stat.st_size),
            "updated_at": datetime.fromtimestamp(
                stat.st_mtime,
                tz=timezone.utc,
            ).isoformat(),
        }
        return meta

    def _delete_impl(self, path: Path) -> None:
        os_path = self._to_os_path(path)
        try:
            os.remove(os_path)
        except FileNotFoundError as exc:  # pragma: no cover
            raise NotFoundError(path) from exc

    # ----------------------------- Tools export -----------------------------
    @staticmethod
    def supported_tools() -> list:
        """Return the set of tool functions supported by this
        implementation."""
        from . import _tools as T
        return [
            T.read_text_file,
            T.read_multiple_files,
            T.list_directory,
            T.list_directory_with_sizes,
            T.search_files,
            T.get_file_info,
            T.list_allowed_directories,
            T.write_file,
            T.delete_file,
            T.edit_file,
        ]

    def get_tools(self, service: object) -> list[tuple[callable, object]]:
        """Return (func, service) pairs for convenient registration."""
        return [(f, service) for f in self.supported_tools()]
