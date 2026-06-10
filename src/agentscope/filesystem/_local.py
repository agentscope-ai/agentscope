# -*- coding: utf-8 -*-
"""Local-disk filesystem implementation with three security modes."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any

from ._abstract import AbstractFilesystem
from ._models import (
    LsResult,
    ReadResult,
    WriteResult,
    EditResult,
    GrepResult,
    GlobResult,
    FileInfo,
    GrepMatch,
)
from ..workspace._path_policy import PathPolicy
from .._logging import logger


class LocalFsMode(str, Enum):
    """Path-resolution policy for host-rooted local filesystem."""

    SANDBOXED = "sandboxed"
    """Anchor all paths to the root; reject ``..`` and escaping absolute paths."""

    ROOTED = "rooted"
    """Allow absolute paths only under configured roots; relative paths resolve
    against the root."""

    UNRESTRICTED = "unrestricted"
    """Absolute paths pass through unchanged."""


class LocalFilesystem(AbstractFilesystem):
    """Direct local-disk implementation using Python stdlib / aiofiles.

    Args:
        root_dir: Filesystem root directory.
        mode: Path resolution mode (default ``SANDBOXED``).
        path_policy: Optional allow-list for ``ROOTED`` mode.
        max_file_size_mb: Files larger than this are read as binary.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        mode: LocalFsMode = LocalFsMode.SANDBOXED,
        path_policy: PathPolicy | None = None,
        max_file_size_mb: int = 10,
    ) -> None:
        self._root = Path(root_dir).resolve()
        self._mode = mode
        self._path_policy = path_policy or PathPolicy.empty()
        self._max_file_size = max_file_size_mb * 1024 * 1024
        # Per-file async locks for edit concurrency safety
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_guard = asyncio.Lock()

    def _resolve(self, path: str) -> Path:
        """Resolve a user-supplied path according to the current mode."""
        p = Path(path)
        if self._mode == LocalFsMode.UNRESTRICTED:
            return p.resolve()
        if self._mode == LocalFsMode.ROOTED:
            if p.is_absolute():
                if not self._path_policy.is_allowed(p):
                    raise PermissionError(
                        f"Path {path!r} is outside allowed roots",
                    )
                return p.resolve()
            return (self._root / p).resolve()
        # SANDBOXED
        stripped = re.sub(r"^[A-Za-z]:[/\\]?", "", str(p))
        stripped = stripped.lstrip("/")
        p = Path(stripped)
        resolved = (self._root / p).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise PermissionError(
                f"Path {path!r} escapes sandbox root {self._root}",
            ) from exc
        return resolved

    async def _get_lock(self, abs_path: str) -> asyncio.Lock:
        async with self._lock_guard:
            if abs_path not in self._locks:
                self._locks[abs_path] = asyncio.Lock()
            return self._locks[abs_path]

    async def ls(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> LsResult:
        resolved = self._resolve(path)
        if not resolved.exists():
            return LsResult(path=path, entries=[])
        entries: list[FileInfo] = []
        for item in sorted(resolved.iterdir(), key=lambda p: p.name):
            stat = item.stat()
            entries.append(
                FileInfo(
                    name=item.name,
                    is_directory=item.is_dir(),
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                ),
            )
        return LsResult(path=path, entries=entries)

    async def read(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> ReadResult:
        resolved = self._resolve(file_path)
        if not resolved.is_file():
            raise FileNotFoundError(f"Not a file: {file_path}")

        size = resolved.stat().st_size
        if size > self._max_file_size:
            return ReadResult(
                path=file_path,
                content="",
                is_binary=True,
                offset=0,
                total_lines=0,
            )

        text = resolved.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)

        if offset < 0:
            offset = 0
        if limit <= 0:
            limit = total
        sliced = lines[offset : offset + limit]
        return ReadResult(
            path=file_path,
            content="\n".join(sliced),
            is_binary=False,
            offset=offset,
            total_lines=total,
        )

    async def write(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        content: str,
    ) -> WriteResult:
        resolved = self._resolve(file_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return WriteResult(path=file_path, bytes_written=len(content.encode("utf-8")))

    async def edit(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        resolved = self._resolve(file_path)
        if not resolved.is_file():
            raise FileNotFoundError(f"Not a file: {file_path}")

        lock = await self._get_lock(str(resolved))
        async with lock:
            original = resolved.read_text(encoding="utf-8", errors="replace")
            if replace_all:
                modified = original.replace(old_string, new_string)
                count = original.count(old_string)
            else:
                modified = original.replace(old_string, new_string, 1)
                count = 1 if old_string in original else 0

            if count == 0:
                return EditResult(
                    path=file_path,
                    replacements=0,
                    original=original,
                    modified=original,
                )

            resolved.write_text(modified, encoding="utf-8")
            return EditResult(
                path=file_path,
                replacements=count,
                original=original,
                modified=modified,
            )

    async def grep(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
        glob: str = "",
    ) -> GrepResult:
        resolved = self._resolve(path)

        # Try ripgrep first for performance
        try:
            cmd = ["rg", "--json", "-H", "-n", "-F", pattern]
            if glob:
                cmd += ["-g", glob]
            cmd.append(str(resolved))
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await proc.communicate()
            if proc.returncode in (0, 1):  # 1 = no matches, still valid
                matches: list[GrepMatch] = []
                for line in stdout.decode("utf-8", errors="replace").splitlines():
                    if not line.startswith('{"type":"match"'):
                        continue
                    # Simplified parsing — enough for unit tests
                    import json
                    obj = json.loads(line)
                    mdata = obj.get("data", {})
                    fpath = mdata.get("path", {}).get("text", "")
                    line_num = mdata.get("line_number", 0)
                    line_text = mdata.get("lines", {}).get("text", "")
                    matches.append(
                        GrepMatch(
                            path=fpath,
                            line_number=line_num,
                            line_text=line_text,
                        ),
                    )
                return GrepResult(pattern=pattern, matches=matches)
        except FileNotFoundError:
            pass  # ripgrep not installed

        # Fallback: pure Python walk
        matches: list[GrepMatch] = []
        needle = re.escape(pattern)
        for root, _dirs, files in os.walk(str(resolved)):
            for fname in files:
                if glob and not self._match_glob(fname, glob):
                    continue
                fpath = Path(root) / fname
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if pattern in line:
                            matches.append(
                                GrepMatch(
                                    path=str(fpath),
                                    line_number=i,
                                    line_text=line,
                                ),
                            )
                except Exception:
                    pass
        return GrepResult(pattern=pattern, matches=matches)

    async def glob(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
    ) -> GlobResult:
        resolved = self._resolve(path)
        # Simple glob using Path.rglob for "**" or Path.glob otherwise
        if "**" in pattern:
            gen = resolved.rglob(pattern.replace("**", "").lstrip("/"))
        else:
            gen = resolved.glob(pattern)
        paths = [str(p) for p in gen if p.is_file()]
        return GlobResult(pattern=pattern, paths=paths)

    async def delete(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> WriteResult:
        resolved = self._resolve(path)
        if not resolved.exists():
            return WriteResult(path=path, bytes_written=0)
        if resolved.is_dir():
            await asyncio.to_thread(shutil.rmtree, str(resolved))
        else:
            await asyncio.to_thread(os.remove, str(resolved))
        return WriteResult(path=path, bytes_written=0)

    async def move(
        self,
        runtime_context: dict[str, Any],
        from_path: str,
        to_path: str,
    ) -> WriteResult:
        src = self._resolve(from_path)
        dst = self._resolve(to_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return WriteResult(path=to_path, bytes_written=0)

    async def exists(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> bool:
        return self._resolve(path).exists()

    @staticmethod
    def _match_glob(filename: str, glob: str) -> bool:
        """Simple glob matcher for fallback grep."""
        import fnmatch
        return fnmatch.fnmatch(filename, glob)
