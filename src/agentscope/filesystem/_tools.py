# -*- coding: utf-8 -*-
"""Public tool functions (单工具集) that depend on FileDomainService.

Register these functions into a Toolkit with `preset_kwargs={"service": svc}`
so that `service` 不出现在 JSON-Schema（即不暴露给 LLM）。
"""
from __future__ import annotations

from typing import Iterable

from ..message import TextBlock
from ..tool._response import ToolResponse
from ._service import FileDomainService


# ------------------------------- read ops -----------------------------------
async def read_text_file(
    service: FileDomainService,
    path: str,
    start_line: int | None = 1,
    read_lines: int | None = None,
) -> ToolResponse:
    text = service.read_text_file(path, start_line=start_line, read_lines=read_lines)
    return ToolResponse(content=[TextBlock(type="text", text=text)])


async def read_multiple_files(
    service: FileDomainService,
    paths: list[str],
) -> ToolResponse:
    items = service.read_multiple_files(paths)
    lines = []
    for it in items:
        if it["ok"]:
            lines.append(f"## {it['path']}\n{it['content']}")
        else:
            lines.append(f"## {it['path']} (error)\n{it['error']}")
    text = "\n\n".join(lines) if lines else "<empty>"
    return ToolResponse(content=[TextBlock(type="text", text=text)])


# ------------------------------ list/search ---------------------------------
async def list_directory(service: FileDomainService, path: str) -> ToolResponse:
    lines = service.list_directory(path)
    return ToolResponse(content=[TextBlock(type="text", text="\n".join(lines) or "<empty>")])


async def list_directory_with_sizes(
    service: FileDomainService,
    path: str,
    sortBy: str | None = "name",
) -> ToolResponse:
    lines, summary = service.list_directory_with_sizes(path, sort_by=(sortBy or "name"))
    text = ("\n".join(lines) + ("\n" if lines else "")) + summary
    return ToolResponse(content=[TextBlock(type="text", text=text)])


async def search_files(
    service: FileDomainService,
    path: str,
    pattern: str,
    excludePatterns: list[str] | None = None,
) -> ToolResponse:
    matches = service.search_files(path, pattern, exclude_patterns=excludePatterns)
    return ToolResponse(content=[TextBlock(type="text", text="\n".join(matches) or "<empty>")])


async def get_file_info(service: FileDomainService, path: str) -> ToolResponse:
    meta = service.get_file_info(path)
    payload = "\n".join(f"{k}: {v}" for k, v in meta.items())
    return ToolResponse(content=[TextBlock(type="text", text=payload)])


async def list_allowed_directories(service: FileDomainService) -> ToolResponse:
    dirs = service.list_allowed_directories()
    return ToolResponse(content=[TextBlock(type="text", text="\n".join(dirs))])


# ------------------------------- mutations ----------------------------------
async def write_file(service: FileDomainService, path: str, content: str) -> ToolResponse:
    meta = service.write_file(path, content)
    text = f"wrote {int(meta.get('size', 0))} bytes to {meta['path']}"
    return ToolResponse(content=[TextBlock(type="text", text=text)])


async def edit_file(
    service: FileDomainService,
    path: str,
    edits: list[dict],
    dryRun: bool | None = False,
) -> ToolResponse:
    res, changed = service.edit_file(path, edits, dry_run=bool(dryRun))
    return ToolResponse(content=[TextBlock(type="text", text=res)])


__all__ = [
    "read_text_file",
    "read_multiple_files",
    "list_directory",
    "list_directory_with_sizes",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
    "write_file",
    "edit_file",
]

