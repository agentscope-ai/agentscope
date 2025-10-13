# -*- coding: utf-8 -*-
"""Helper to assemble BuiltinFileSystem × Toolkit using the SOP's single route.

This module exposes a single factory that:
- Creates a BuiltinFileSystem and obtains the workspace handle
- Defines tools via closures (MUST): each tool signature only contains
  JSON-serializable parameters; the FsHandle is captured by the closure and
  never appears in the function signature nor in the generated JSON Schema
- Registers tools to a Toolkit and returns both the filesystem and toolkit

The exported API is intentionally minimal and follows docs/filesystem/SOP.md.
"""
from __future__ import annotations

from typing import Tuple

from ._builtin import BuiltinFileSystem, WORKSPACE_PREFIX
from ..message import TextBlock
from ..tool._toolkit import Toolkit
from ..tool._response import ToolResponse


def create_filesystem_toolkit() -> Tuple[BuiltinFileSystem, Toolkit]:
    """Create a BuiltinFileSystem and a Toolkit with workspace tools registered.

    Tools (all closures capturing the workspace handle):
    - ws_list(prefix: str | None = None) -> ToolResponse
    - ws_file(path: str) -> ToolResponse
    - ws_read_file(path: str, index: int | None = None, line: int | None = None) -> ToolResponse
    - ws_read_re(path: str, pattern: str, overlap: int | None = None) -> ToolResponse
    - ws_write(path: str, data: str, overwrite: bool = True) -> ToolResponse
    - ws_delete(path: str) -> ToolResponse
    """

    fs = BuiltinFileSystem()
    handle = fs.create_workspace_handle()

    async def ws_list(prefix: str | None = None) -> ToolResponse:
        entries = handle.list(prefix)
        text = "\n".join(meta["path"] for meta in entries) or "no entries"
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    async def ws_file(path: str) -> ToolResponse:
        meta = handle.file(path)
        payload = "\n".join(f"{k}: {v}" for k, v in meta.items())
        return ToolResponse(content=[TextBlock(type="text", text=payload)])

    async def ws_read_file(
        path: str,
        index: int | None = None,
        line: int | None = None,
    ) -> ToolResponse:
        out = handle.read_file(path, index=index, line=line)
        return ToolResponse(content=[TextBlock(type="text", text=out or "<empty>")])

    async def ws_read_re(
        path: str,
        pattern: str,
        overlap: int | None = None,
    ) -> ToolResponse:
        matches = handle.read_re(path, pattern, overlap=overlap)
        text = "\n".join(matches) if matches else "no matches"
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    async def ws_write(path: str, data: str, overwrite: bool = True) -> ToolResponse:
        meta = handle.write(path, data, overwrite=overwrite)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"wrote {meta['size']} bytes to {meta['path']}",
                ),
            ],
        )

    async def ws_delete(path: str) -> ToolResponse:
        handle.delete(path)
        return ToolResponse(content=[TextBlock(type="text", text=f"deleted {path}")])

    tk = Toolkit()
    tk.register_tool_function(ws_list)
    tk.register_tool_function(ws_file)
    tk.register_tool_function(ws_read_file)
    tk.register_tool_function(ws_read_re)
    tk.register_tool_function(ws_write)
    tk.register_tool_function(ws_delete)

    # Seed example content for quick verification
    seed = "\n".join(["foo a", "bar b", "foo c"])
    handle.write(f"{WORKSPACE_PREFIX}hello.txt", seed, overwrite=True)

    return fs, tk


__all__ = ["create_filesystem_toolkit"]
