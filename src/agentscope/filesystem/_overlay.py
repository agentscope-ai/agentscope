# -*- coding: utf-8 -*-
"""Copy-on-write layering. Upper (writable) masks lower (read-only)."""
from __future__ import annotations

from typing import Any

from ._abstract import AbstractFilesystem
from ._models import LsResult, ReadResult, WriteResult, EditResult, GrepResult, GlobResult


class OverlayFilesystem(AbstractFilesystem):
    """Two-layer copy-on-write filesystem.

    Reads check the *upper* layer first, then fall back to *lower*.
    All writes go exclusively to the *upper* layer.

    Args:
        upper: Writable overlay layer.
        lower: Read-only base layer.
    """

    def __init__(
        self,
        upper: AbstractFilesystem,
        lower: AbstractFilesystem,
    ) -> None:
        self._upper = upper
        self._lower = lower

    async def ls(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> LsResult:
        upper_result = await self._upper.ls(runtime_context, path)
        if upper_result.entries:
            return upper_result
        return await self._lower.ls(runtime_context, path)

    async def read(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> ReadResult:
        if await self._upper.exists(runtime_context, file_path):
            return await self._upper.read(runtime_context, file_path, offset, limit)
        return await self._lower.read(runtime_context, file_path, offset, limit)

    async def write(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        content: str,
    ) -> WriteResult:
        return await self._upper.write(runtime_context, file_path, content)

    async def edit(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        # If file exists in upper, edit there. Otherwise read from lower,
        # write merged to upper, then edit.
        if not await self._upper.exists(runtime_context, file_path):
            original = await self._lower.read(runtime_context, file_path)
            await self._upper.write(runtime_context, file_path, original.content)
        return await self._upper.edit(
            runtime_context, file_path, old_string, new_string, replace_all
        )

    async def grep(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
        glob: str = "",
    ) -> GrepResult:
        upper = await self._upper.grep(runtime_context, pattern, path, glob)
        lower = await self._lower.grep(runtime_context, pattern, path, glob)
        # Mask lower results with upper (upper wins on same path+line)
        seen = {(m.path, m.line_number) for m in upper.matches}
        merged = list(upper.matches)
        for m in lower.matches:
            if (m.path, m.line_number) not in seen:
                merged.append(m)
        return GrepResult(pattern=pattern, matches=merged)

    async def glob(
        self,
        runtime_context: dict[str, Any],
        pattern: str,
        path: str,
    ) -> GlobResult:
        upper = await self._upper.glob(runtime_context, pattern, path)
        lower = await self._lower.glob(runtime_context, pattern, path)
        seen = set(upper.paths)
        merged = list(upper.paths)
        for p in lower.paths:
            if p not in seen:
                merged.append(p)
        return GlobResult(pattern=pattern, paths=merged)

    async def delete(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> WriteResult:
        # Delete from upper only; lower is read-only
        return await self._upper.delete(runtime_context, path)

    async def move(
        self,
        runtime_context: dict[str, Any],
        from_path: str,
        to_path: str,
    ) -> WriteResult:
        # Ensure source is visible in upper before moving
        if not await self._upper.exists(runtime_context, from_path):
            if await self._lower.exists(runtime_context, from_path):
                data = await self._lower.read(runtime_context, from_path)
                await self._upper.write(runtime_context, from_path, data.content)
        return await self._upper.move(runtime_context, from_path, to_path)

    async def exists(
        self,
        runtime_context: dict[str, Any],
        path: str,
    ) -> bool:
        return (
            await self._upper.exists(runtime_context, path)
            or await self._lower.exists(runtime_context, path)
        )
