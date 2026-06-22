# -*- coding: utf-8 -*-
"""Filesystem backend enforcing an actor's workspace path boundaries."""

import os
from collections.abc import Iterable

from ..tool._builtin._backend import BackendBase, ExecResult


class ScopedBackend(BackendBase):
    """Restrict reads and writes before delegating to a physical backend."""

    def __init__(
        self,
        backend: BackendBase,
        *,
        private_root: str,
        shared_root: str,
    ) -> None:
        self._backend = backend
        self.private_root = os.path.normpath(private_root)
        self.shared_root = os.path.normpath(shared_root)

    @staticmethod
    def _within(path: str, roots: Iterable[str]) -> bool:
        normalized = os.path.realpath(path)
        return any(
            normalized == os.path.realpath(root)
            or normalized.startswith(os.path.realpath(root) + os.sep)
            for root in roots
        )

    def _read_path(self, path: str) -> str:
        if not self._within(path, (self.private_root, self.shared_root)):
            raise PermissionError(
                f"Path is outside this workspace view: {path}"
            )
        return path

    def _write_path(self, path: str) -> str:
        if not self._within(path, (self.private_root,)):
            raise PermissionError(f"Path is not writable in this view: {path}")
        return path

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        workdir = cwd or self.private_root
        self._write_path(workdir)
        for value in command[1:]:
            if ".." in value.replace("\\", "/").split("/"):
                raise PermissionError("Parent traversal is not allowed.")
            if os.path.isabs(value):
                self._read_path(value)
        return await self._backend.exec_shell(
            command,
            cwd=workdir,
            timeout=timeout,
        )

    async def read_file(self, path: str) -> bytes:
        return await self._backend.read_file(self._read_path(path))

    async def write_file(self, path: str, data: bytes) -> None:
        await self._backend.write_file(self._write_path(path), data)

    async def file_exists(self, path: str) -> bool:
        return await self._backend.file_exists(self._read_path(path))

    async def is_dir(self, path: str) -> bool:
        return await self._backend.is_dir(self._read_path(path))

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        return await self._backend.list_dir(
            self._read_path(path),
            recursive=recursive,
        )

    async def stat_mtime(self, path: str) -> float | None:
        return await self._backend.stat_mtime(self._read_path(path))

    async def delete_path(self, path: str) -> None:
        await self._backend.delete_path(self._write_path(path))
