# -*- coding: utf-8 -*-
"""KV-store-backed filesystem with optimistic concurrency control."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ._abstract import AbstractFilesystem
from ._base_store import BaseStore, StoreKey, StoreValue
from ._namespace_factory import NamespaceFactory
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
from .._logging import logger


class RemoteFilesystem(AbstractFilesystem):
    """Adapts a :class:`BaseStore` to the :class:`AbstractFilesystem` interface.

    File metadata and directory listings are maintained in a JSON index
    stored under the ``__index__`` key within each namespace.

    Args:
        store: Backing KV store.
        namespace_factory: Produces per-runtime namespaces.
    """

    def __init__(
        self,
        store: BaseStore,
        namespace_factory: NamespaceFactory | None = None,
    ) -> None:
        self._store = store
        self._ns_factory = namespace_factory or NamespaceFactory()
        self._edit_retries = 5

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ns(self, ctx: dict[str, Any]) -> str:
        return self._ns_factory.from_runtime_context(ctx)

    def _key(self, ns: str, path: str) -> StoreKey:
        norm = path.replace("\\", "/")
        return StoreKey(namespace=ns, key=f"file:{norm}")

    def _idx_key(self, ns: str) -> StoreKey:
        return StoreKey(namespace=ns, key="__index__")

    async def _load_index(self, ns: str) -> dict[str, Any]:
        val = await self._store.get(self._idx_key(ns))
        if val is None:
            return {"files": {}, "dirs": {}}
        return json.loads(val.data.decode("utf-8"))

    async def _save_index(self, ns: str, index: dict[str, Any]) -> None:
        data = json.dumps(index, separators=(",", ":")).encode("utf-8")
        await self._store.put(self._idx_key(ns), data)

    async def _read_meta(self, ns: str, path: str) -> dict[str, Any] | None:
        idx = await self._load_index(ns)
        return idx["files"].get(path)

    async def _write_meta(
        self,
        ns: str,
        path: str,
        meta: dict[str, Any],
    ) -> None:
        idx = await self._load_index(ns)
        idx["files"][path] = meta
        # Rebuild parent dirs
        parts = Path(path).parts
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            idx["dirs"].setdefault(parent, {"children": []})
        await self._save_index(ns, idx)

    async def _remove_meta(self, ns: str, path: str) -> None:
        idx = await self._load_index(ns)
        idx["files"].pop(path, None)
        await self._save_index(ns, idx)

    # ------------------------------------------------------------------
    # AbstractFilesystem implementation
    # ------------------------------------------------------------------

    async def ls(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> LsResult:
        ns = self._ns(runtime_context)
        idx = await self._load_index(ns)
        entries: list[FileInfo] = []

        # Files directly under this path
        prefix = path.rstrip("/") + "/" if path not in ("/", "") else ""
        for fpath, meta in idx["files"].items():
            if fpath.startswith(prefix):
                remainder = fpath[len(prefix) :]
                if "/" not in remainder:
                    entries.append(
                        FileInfo(
                            name=remainder,
                            is_directory=False,
                            size_bytes=meta.get("size", 0),
                            modified_at=meta.get("mtime", 0),
                        ),
                    )

        # Subdirectories
        for dpath in idx["dirs"]:
            if dpath.startswith(prefix):
                remainder = dpath[len(prefix) :]
                if "/" not in remainder and remainder:
                    entries.append(
                        FileInfo(
                            name=remainder,
                            is_directory=True,
                            size_bytes=0,
                            modified_at=0,
                        ),
                    )

        # Deduplicate
        seen = set()
        unique: list[FileInfo] = []
        for e in entries:
            if e.name not in seen:
                seen.add(e.name)
                unique.append(e)
        unique.sort(key=lambda e: e.name)
        return LsResult(path=path, entries=unique)

    async def read(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> ReadResult:
        ns = self._ns(runtime_context)
        val = await self._store.get(self._key(ns, file_path))
        if val is None:
            raise FileNotFoundError(f"No such file: {file_path}")
        text = val.data.decode("utf-8", errors="replace")
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
        ns = self._ns(runtime_context)
        data = content.encode("utf-8")
        # Create-if-absent via CAS with expected_version=0
        result = await self._store.put_if_version(
            self._key(ns, file_path),
            data,
            expected_version=0,
        )
        if result is None:
            # File exists — fall back to unconditional put (last-write-wins)
            result = await self._store.put(self._key(ns, file_path), data)

        await self._write_meta(
            ns,
            file_path,
            {
                "size": len(data),
                "mtime": __import__("time").time(),
                "hash": hashlib.sha256(data).hexdigest()[:16],
            },
        )
        return WriteResult(path=file_path, bytes_written=len(data))

    async def edit(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        ns = self._ns(runtime_context)
        key = self._key(ns, file_path)

        for attempt in range(self._edit_retries):
            current = await self._store.get(key)
            if current is None:
                raise FileNotFoundError(f"No such file: {file_path}")

            original = current.data.decode("utf-8", errors="replace")
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

            new_data = modified.encode("utf-8")
            result = await self._store.put_if_version(
                key,
                new_data,
                expected_version=current.version,
            )
            if result is not None:
                await self._write_meta(
                    ns,
                    file_path,
                    {
                        "size": len(new_data),
                        "mtime": __import__("time").time(),
                        "hash": hashlib.sha256(new_data).hexdigest()[:16],
                    },
                )
                return EditResult(
                    path=file_path,
                    replacements=count,
                    original=original,
                    modified=modified,
                )
            # CAS failed — retry
            logger.debug(
                "RemoteFilesystem.edit CAS conflict on %s (attempt %d)",
                file_path,
                attempt + 1,
            )

        raise RuntimeError(
            f"Failed to edit {file_path} after {self._edit_retries} CAS retries",
        )

    async def grep(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
        glob: str = "",
    ) -> GrepResult:
        ns = self._ns(runtime_context)
        idx = await self._load_index(ns)
        matches: list[GrepMatch] = []

        prefix = path.rstrip("/") + "/" if path not in ("/", "") else ""
        for fpath in idx["files"]:
            if not fpath.startswith(prefix):
                continue
            if glob and not self._match_glob(fpath, glob):
                continue
            val = await self._store.get(self._key(ns, fpath))
            if val is None:
                continue
            text = val.data.decode("utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if pattern in line:
                    matches.append(
                        GrepMatch(
                            path=fpath,
                            line_number=i,
                            line_text=line,
                        ),
                    )
        return GrepResult(pattern=pattern, matches=matches)

    async def glob(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
    ) -> GlobResult:
        ns = self._ns(runtime_context)
        idx = await self._load_index(ns)
        paths: list[str] = []

        prefix = path.rstrip("/") + "/" if path not in ("/", "") else ""
        for fpath in idx["files"]:
            if not fpath.startswith(prefix):
                continue
            remainder = fpath[len(prefix) :]
            if self._match_glob(remainder, pattern):
                paths.append(fpath)
        return GlobResult(pattern=pattern, paths=paths)

    async def delete(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> WriteResult:
        ns = self._ns(runtime_context)
        removed = await self._store.delete(self._key(ns, path))
        await self._remove_meta(ns, path)
        return WriteResult(path=path, bytes_written=0)

    async def move(
        self,
        runtime_context: dict[str, Any],
        from_path: str,
        to_path: str,
    ) -> WriteResult:
        ns = self._ns(runtime_context)
        src_key = self._key(ns, from_path)
        dst_key = self._key(ns, to_path)

        val = await self._store.get(src_key)
        if val is None:
            raise FileNotFoundError(f"No such file: {from_path}")

        await self._store.put(dst_key, val.data)
        await self._store.delete(src_key)

        meta = await self._read_meta(ns, from_path)
        if meta:
            await self._write_meta(ns, to_path, meta)
            await self._remove_meta(ns, from_path)

        return WriteResult(path=to_path, bytes_written=len(val.data))

    async def exists(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> bool:
        ns = self._ns(runtime_context)
        val = await self._store.get(self._key(ns, path))
        return val is not None

    @staticmethod
    def _match_glob(path: str, pattern: str) -> bool:
        import fnmatch
        # Support simple ** patterns
        if "**" in pattern:
            parts = pattern.split("**")
            if len(parts) == 2:
                prefix, suffix = parts
                return path.startswith(prefix) and path.endswith(suffix)
        return fnmatch.fnmatch(path, pattern)
