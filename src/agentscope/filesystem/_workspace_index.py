# -*- coding: utf-8 -*-
"""SQLite-backed workspace index for fast directory listings."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from .._logging import logger


class WorkspaceIndex:
    """Maintains an on-disk SQLite index of file metadata for a workspace.

    This is an optional accelerator for :class:`RemoteFilesystem` when the
    backing store does not provide efficient prefix scans.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db = Path(db_path)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(str(self._db)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL DEFAULT 0,
                    mtime REAL NOT NULL DEFAULT 0,
                    hash_prefix TEXT
                )
                """,
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_path_prefix ON files(path)
                """,
            )
            conn.commit()

    async def upsert(
        self,
        path: str,
        size: int = 0,
        mtime: float = 0.0,
        hash_prefix: str | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._upsert_sync, path, size, mtime, hash_prefix)

    def _upsert_sync(
        self,
        path: str,
        size: int,
        mtime: float,
        hash_prefix: str | None,
    ) -> None:
        with sqlite3.connect(str(self._db)) as conn:
            conn.execute(
                """
                INSERT INTO files (path, size, mtime, hash_prefix)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size=excluded.size,
                    mtime=excluded.mtime,
                    hash_prefix=excluded.hash_prefix
                """,
                (path, size, mtime, hash_prefix),
            )
            conn.commit()

    async def remove(self, path: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._remove_sync, path)

    def _remove_sync(self, path: str) -> None:
        with sqlite3.connect(str(self._db)) as conn:
            conn.execute("DELETE FROM files WHERE path = ?", (path,))
            conn.commit()

    async def list_children(self, prefix: str) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_children_sync, prefix)

    def _list_children_sync(self, prefix: str) -> list[dict[str, Any]]:
        # Ensure prefix ends with /
        if not prefix.endswith("/"):
            prefix += "/"
        with sqlite3.connect(str(self._db)) as conn:
            cursor = conn.execute(
                "SELECT path, size, mtime FROM files WHERE path LIKE ?",
                (prefix + "%",),
            )
            rows = cursor.fetchall()
        # Extract immediate children only
        children: dict[str, dict[str, Any]] = {}
        for row in rows:
            rel = row[0][len(prefix) :]
            first_segment = rel.split("/", 1)[0]
            if first_segment not in children:
                is_dir = "/" in rel
                children[first_segment] = {
                    "name": first_segment,
                    "is_directory": is_dir,
                    "size_bytes": 0 if is_dir else row[1],
                    "modified_at": 0.0 if is_dir else row[2],
                }
        return sorted(children.values(), key=lambda x: x["name"])

    async def clear(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> None:
        with sqlite3.connect(str(self._db)) as conn:
            conn.execute("DELETE FROM files")
            conn.commit()
