# -*- coding: utf-8 -*-
"""Public tool functions (单工具集) that depend on FileDomainService.

Register these functions into a Toolkit with `preset_kwargs={"service": svc}`
so that `service` 不出现在 JSON-Schema（即不暴露给 LLM）。
"""
from __future__ import annotations


# ------------------------------- read ops -----------------------------------
async def read_text_file(
    service: object,
    path: str,
    start_line: int | None = 1,
    read_lines: int | None = None,
    with_line_numbers: bool | None = True,
    with_header: bool | None = True,
):
    """Read text from a logical file; optional line numbers + header.

    Directory roles: read from /userinput/ (user‑provided, read‑only) or
    /workspace/ (assistant output). Never modifies files. Trigger when the
    user requests to view/quote file content or specific lines; avoid for
    binary or unknown paths.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    raw_text = service.read_text_file(
        path,
        start_line=start_line,
        read_lines=read_lines,
    )

    # Prepare numbered rendering
    if with_line_numbers:
        lines = raw_text.splitlines()
        numbered = [
            f"line{i}: {line}" for i, line in enumerate(lines, start=1)
        ]

        if with_header:
            header = [
                f"line_count: {len(lines)}",
                "format: line{N}: <content>",
            ]
            text_out = "\n".join([*header, *numbered])
        else:
            text_out = "\n".join(numbered)
    else:
        # Raw text path
        if with_header:
            lines = raw_text.splitlines()
            header = [
                f"line_count: {len(lines)}",
                "format: raw",
            ]
            if raw_text:
                text_out = "\n".join([*header, raw_text])
            else:
                text_out = "\n".join(header)
        else:
            text_out = raw_text

    return _TR(content=[_TB(type="text", text=text_out)])


async def read_multiple_files(
    service: object,
    paths: list[str],
    with_line_numbers: bool | None = True,
    with_header: bool | None = True,
):
    """Read multiple logical files; per‑file line numbers/header optional.

    Directory roles as above. Use to compare/aggregate content across files;
    errors are reported inline per file.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    items = service.read_multiple_files(paths)
    sections: list[str] = []
    for it in items:
        path = it.get("path", "<unknown>")
        if it.get("ok"):
            content = it.get("content", "")
            lines = content.splitlines()
            header_parts: list[str] = []
            if with_header:
                header_parts = [
                    f"line_count: {len(lines)}",
                    (
                        "format: line{N}: <content>"
                        if with_line_numbers
                        else "format: raw"
                    ),
                ]

            if with_line_numbers:
                body = "\n".join(
                    f"line{i}: {line}" for i, line in enumerate(lines, start=1)
                )
            else:
                body = content

            section = [f"## {path}"]
            if header_parts:
                section.extend(header_parts)
            if body:
                section.append(body)
            sections.append("\n".join(section))
        else:
            sections.append(f"## {path} (error)\n{it.get('error', '')}")

    text = "\n\n".join(sections) if sections else "<empty>"
    return _TR(content=[_TB(type="text", text=text)])


# ------------------------------ list/search ---------------------------------
async def list_directory(service: object, path: str):
    """List immediate children of a logical directory.

    Directory roles as above. Use to browse a folder (e.g., /workspace/) and
    discover files. Output: one line per child labeled [DIR]/[FILE]; returns
    <empty> if none.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    lines = service.list_directory(path)
    return _TR(content=[_TB(type="text", text="\n".join(lines) or "<empty>")])


async def list_directory_with_sizes(
    service: object,
    path: str,
    sortBy: str | None = "name",
):
    """List children with aggregated sizes and a summary (sortBy=name|size).

    Directory roles as above. Use for directory overview or to estimate size
    before further actions.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    lines, summary = service.list_directory_with_sizes(
        path,
        sort_by=(sortBy or "name"),
    )
    text = ("\n".join(lines) + ("\n" if lines else "")) + summary
    return _TR(content=[_TB(type="text", text=text)])


async def search_files(
    service: object,
    path: str,
    pattern: str,
    excludePatterns: list[str] | None = None,
):
    """Search recursively under a logical root (glob/substring).

    Directory roles as above. Use to locate files by name/extension; use
    excludePatterns to prune noise. Output: matched paths per line or
    <empty>.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    matches = service.search_files(
        path,
        pattern,
        exclude_patterns=excludePatterns,
    )
    text = "\n".join(matches) or "<empty>"
    return _TR(content=[_TB(type="text", text=text)])


async def get_file_info(service: object, path: str):
    """Return metadata for a logical file (path, size, updated_at).

    Directory roles as above. Use for audit or before edits/deletion.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    meta = service.get_file_info(path)
    payload = "\n".join(f"{k}: {v}" for k, v in meta.items())
    return _TR(content=[_TB(type="text", text=payload)])


async def list_allowed_directories(service: object):
    """List allowed top‑level logical roots (e.g., /userinput/, /workspace/).

    Use when uncertain which directories are operable for subsequent
    list/read/write operations.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    dirs = service.list_allowed_directories()
    return _TR(content=[_TB(type="text", text="\n".join(dirs))])


# ------------------------------- mutations ----------------------------------
async def write_file(service: object, path: str, content: str):
    """Overwrite a logical file with UTF‑8 text; report bytes written.

    Directory roles: write only under /workspace/ (assistant output);
    refuse /userinput/. Use when the user asks to create/update results; not
    for large/binary payloads.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    meta = service.write_file(path, content)
    text = f"wrote {int(meta.get('size', 0))} bytes to {meta['path']}"
    return _TR(content=[_TB(type="text", text=text)])


async def delete_file(service: object, path: str):
    """Delete a logical file (policy‑aware) and confirm the path.

    Directory roles: delete only under /workspace/; refuse /userinput/.
    Use only when the user explicitly requests removal.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    service.delete_file(path)
    return _TR(content=[_TB(type="text", text=f"deleted {path}")])


async def edit_file(
    service: object,
    path: str,
    edits: list[dict],
):
    """Apply ordered textual replacements then overwrite the file.

    Directory roles as above. Use for small, deterministic edits (search &
    replace). Not suitable for binary or large refactors.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    meta = service.edit_file(path, edits)
    text = f"edited {meta['path']} (size={int(meta.get('size', 0))})"
    return _TR(content=[_TB(type="text", text=text)])


async def fs_describe_permissions_markdown(service: object):
    """Summarize current filesystem grants as human‑readable markdown.

    Use when the agent needs to understand which logical prefixes it can
    access and with what operations; purely diagnostic, no writes.
    """
    from ..tool._response import ToolResponse as _TR
    from ..message import TextBlock as _TB

    text = service.describe_permissions_markdown()
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
    "fs_describe_permissions_markdown",
]
