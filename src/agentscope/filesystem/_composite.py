# -*- coding: utf-8 -*-
"""Prefix-based router that shards paths across multiple backends."""
from __future__ import annotations

from typing import Any

from ._abstract import AbstractFilesystem
from ._models import LsResult, ReadResult, WriteResult, EditResult, GrepResult, GlobResult


class CompositeFilesystem(AbstractFilesystem):
    """Routes paths to the longest-matching :class:`AbstractFilesystem` backend.

    Unmatched paths fall through to the *default* backend (if any).

    Example::

        composite = CompositeFilesystem(default=local_fs)
        composite.mount("/tmp", tmp_fs)
        composite.mount("/projects", project_fs)
    """

    def __init__(
        self,
        default: AbstractFilesystem | None = None,
    ) -> None:
        self._default = default
        self._mounts: dict[str, AbstractFilesystem] = {}

    def mount(self, prefix: str, fs: AbstractFilesystem) -> None:
        """Attach *fs* for all paths starting with *prefix*."""
        # Normalise prefix to start with /
        prefix = prefix if prefix.startswith("/") else "/" + prefix
        self._mounts[prefix] = fs

    def unmount(self, prefix: str) -> AbstractFilesystem | None:
        """Remove a mount and return the previous backend (if any)."""
        prefix = prefix if prefix.startswith("/") else "/" + prefix
        return self._mounts.pop(prefix, None)

    def _route(self, path: str) -> tuple[AbstractFilesystem, str]:
        """Return the backend and the relative path within that backend."""
        candidates = sorted(self._mounts.keys(), key=len, reverse=True)
        for prefix in candidates:
            if prefix == "/":
                # Root mount matches everything
                return self._mounts[prefix], path
            if path == prefix or path.startswith(prefix + "/"):
                rel = path[len(prefix) :] or "/"
                return self._mounts[prefix], rel
        if self._default is None:
            raise RuntimeError(f"No backend mounted for path: {path!r}")
        return self._default, path

    async def ls(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> LsResult:
        fs, rel = self._route(path)
        return await fs.ls(runtime_context, rel)

    async def read(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> ReadResult:
        fs, rel = self._route(file_path)
        return await fs.read(runtime_context, rel, offset, limit)

    async def write(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        content: str,
    ) -> WriteResult:
        fs, rel = self._route(file_path)
        return await fs.write(runtime_context, rel, content)

    async def edit(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        fs, rel = self._route(file_path)
        return await fs.edit(runtime_context, rel, old_string, new_string, replace_all)

    async def grep(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
        glob: str = "",
    ) -> GrepResult:
        # Aggregate across all backends for root-level searches
        if path in ("/", ""):
            return await self._aggregate_grep(runtime_context, pattern, glob)
        fs, rel = self._route(path)
        return await fs.grep(runtime_context, pattern, rel, glob)

    async def glob(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
    ) -> GlobResult:
        if path in ("/", ""):
            return await self._aggregate_glob(runtime_context, pattern)
        fs, rel = self._route(path)
        return await fs.glob(runtime_context, pattern, rel)

    async def delete(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> WriteResult:
        fs, rel = self._route(path)
        return await fs.delete(runtime_context, rel)

    async def move(
        self,
        runtime_context: dict[str, Any],
        from_path: str,
        to_path: str,
    ) -> WriteResult:
        src_fs, src_rel = self._route(from_path)
        dst_fs, dst_rel = self._route(to_path)
        if src_fs is dst_fs:
            return await src_fs.move(runtime_context, src_rel, dst_rel)
        # Cross-backend: read → write → delete fallback
        data = await src_fs.read(runtime_context, src_rel)
        result = await dst_fs.write(runtime_context, dst_rel, data.content)
        await src_fs.delete(runtime_context, src_rel)
        return result

    async def exists(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> bool:
        fs, rel = self._route(path)
        return await fs.exists(runtime_context, rel)

    # ------------------------------------------------------------------
    # Aggregation helpers for root-level searches
    # ------------------------------------------------------------------

    async def _aggregate_grep(
        self,
        ctx: dict[str, Any],
        pattern: str,
        glob: str,
    ) -> GrepResult:
        all_matches = []
        seen = set()
        backends = list(self._mounts.values())
        if self._default:
            backends.append(self._default)
        for fs in backends:
            result = await fs.grep(ctx, pattern, "/", glob)
            for m in result.matches:
                key = (m.path, m.line_number)
                if key not in seen:
                    seen.add(key)
                    all_matches.append(m)
        return GrepResult(pattern=pattern, matches=all_matches)

    async def _aggregate_glob(
        self,
        ctx: dict[str, Any],
        pattern: str,
    ) -> GlobResult:
        all_paths = []
        seen = set()
        backends = list(self._mounts.values())
        if self._default:
            backends.append(self._default)
        for fs in backends:
            result = await fs.glob(ctx, pattern, "/")
            for p in result.paths:
                if p not in seen:
                    seen.add(p)
                    all_paths.append(p)
        return GlobResult(pattern=pattern, paths=all_paths)
