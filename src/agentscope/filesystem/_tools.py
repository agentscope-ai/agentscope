# -*- coding: utf-8 -*-
"""Public tool functions (单工具集) that depend on FileDomainService.

Register these functions into a Toolkit with `preset_kwargs={"service": svc}`
so that `service` 不出现在 JSON-Schema（即不暴露给 LLM）。
"""
from __future__ import annotations

from typing import Iterable

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type hints only
    from ..tool._response import ToolResponse  # pragma: no cover
    from ..message import TextBlock  # pragma: no cover
    from ._service import FileDomainService  # pragma: no cover


# ------------------------------- read ops -----------------------------------
async def read_text_file(
    service: object,
    path: str,
    start_line: int | None = 1,
    read_lines: int | None = None,
):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    text = service.read_text_file(path, start_line=start_line, read_lines=read_lines)
    return _TR(content=[_TB(type="text", text=text)])


async def read_multiple_files(
    service: object,
    paths: list[str],
):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    items = service.read_multiple_files(paths)
    lines = []
    for it in items:
        if it["ok"]:
            lines.append(f"## {it['path']}\n{it['content']}")
        else:
            lines.append(f"## {it['path']} (error)\n{it['error']}")
    text = "\n\n".join(lines) if lines else "<empty>"
    return _TR(content=[_TB(type="text", text=text)])


# ------------------------------ list/search ---------------------------------
async def list_directory(service: object, path: str):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    lines = service.list_directory(path)
    return _TR(content=[_TB(type="text", text="\n".join(lines) or "<empty>")])


async def list_directory_with_sizes(
    service: object,
    path: str,
    sortBy: str | None = "name",
):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    lines, summary = service.list_directory_with_sizes(path, sort_by=(sortBy or "name"))
    text = ("\n".join(lines) + ("\n" if lines else "")) + summary
    return _TR(content=[_TB(type="text", text=text)])


async def search_files(
    service: object,
    path: str,
    pattern: str,
    excludePatterns: list[str] | None = None,
):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    matches = service.search_files(path, pattern, exclude_patterns=excludePatterns)
    return _TR(content=[_TB(type="text", text="\n".join(matches) or "<empty>")])


async def get_file_info(service: object, path: str):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    meta = service.get_file_info(path)
    payload = "\n".join(f"{k}: {v}" for k, v in meta.items())
    return _TR(content=[_TB(type="text", text=payload)])


async def list_allowed_directories(service: object):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    dirs = service.list_allowed_directories()
    return _TR(content=[_TB(type="text", text="\n".join(dirs))])


# ------------------------------- mutations ----------------------------------
async def write_file(service: object, path: str, content: str):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    meta = service.write_file(path, content)
    text = f"wrote {int(meta.get('size', 0))} bytes to {meta['path']}"
    return _TR(content=[_TB(type="text", text=text)])


async def delete_file(service: object, path: str):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    service.delete_file(path)
    return _TR(content=[_TB(type="text", text=f"deleted {path}")])


async def edit_file(
    service: object,
    path: str,
    edits: list[dict],
):
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB
    meta = service.edit_file(path, edits)
    text = f"edited {meta['path']} (size={int(meta.get('size', 0))})"
    return _TR(content=[_TB(type="text", text=text)])

__all__ = [
    "read_text_file",
    "read_multiple_files",
    "list_directory",
    "list_directory_with_sizes",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
    "write_file",
    "delete_file",
    "edit_file",
]
