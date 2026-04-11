# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=line-too-long
"""The grep search tool in agentscope."""
import os
import re

from .._response import ToolResponse
from ...message import TextBlock


async def grep_search(
    pattern: str,
    directory: str = ".",
    include: str | None = None,
    case_sensitive: bool = True,
    max_results: int = 50,
    context_lines: int = 0,
) -> ToolResponse:
    """Search for a regex pattern in file contents under the given directory. Returns matching lines with file paths and line numbers.

    Args:
        pattern (`str`):
            The regex pattern to search for in file contents.
        directory (`str`, defaults to `"."`):
            The directory to search in. Defaults to the current working directory.
        include (`str | None`, defaults to `None`):
            A glob pattern to filter files (e.g. "*.py", "*.js"). If not provided, all text files will be searched.
        case_sensitive (`bool`, defaults to `True`):
            Whether the search is case-sensitive.
        max_results (`int`, defaults to `50`):
            The maximum number of matching lines to return.
        context_lines (`int`, defaults to `0`):
            The number of context lines to show before and after each match.

    Returns:
        `ToolResponse`:
            The tool response containing the matching lines or an error message.
    """
    directory = os.path.expanduser(directory)

    if not os.path.exists(directory):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The directory {directory} does not exist.",
                ),
            ],
        )

    if not os.path.isdir(directory):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The path {directory} is not a directory.",
                ),
            ],
        )

    # Compile the regex pattern
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled_pattern = re.compile(pattern, flags)
    except re.error as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Invalid regex pattern: {e}",
                ),
            ],
        )

    # Compile the include glob pattern if provided
    include_pattern = include

    # Binary file extensions to skip
    binary_extensions = {
        ".pyc", ".pyo", ".so", ".o", ".a", ".lib", ".dll", ".dylib",
        ".exe", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
        ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".woff", ".woff2", ".ttf", ".eot",
        ".class", ".jar", ".war",
    }

    results = []
    files_searched = 0
    total_matches = 0

    for root, _dirs, files in os.walk(directory):
        # Skip hidden directories and common non-code directories
        rel_root = os.path.relpath(root, directory)
        parts = rel_root.split(os.sep)
        if any(
            p.startswith(".") or p in ("node_modules", "__pycache__", "venv")
            for p in parts
            if p != "."
        ):
            continue

        for filename in sorted(files):
            if filename.startswith("."):
                continue

            # Check file extension
            _, ext = os.path.splitext(filename)
            if ext.lower() in binary_extensions:
                continue

            # Apply include filter
            if include_pattern is not None:
                import fnmatch

                if not fnmatch.fnmatch(filename, include_pattern):
                    continue

            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, directory)

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except (OSError, PermissionError):
                continue

            files_searched += 1

            # Find matching lines
            match_indices = []
            for i, line in enumerate(lines):
                if compiled_pattern.search(line):
                    match_indices.append(i)

            for idx in match_indices:
                if total_matches >= max_results:
                    break

                # Collect context lines
                start = max(0, idx - context_lines)
                end = min(len(lines), idx + context_lines + 1)

                context_block = []
                for i in range(start, end):
                    prefix = ">" if i == idx else " "
                    line_text = lines[i].rstrip("\n\r")
                    context_block.append(
                        f"{prefix} {i + 1:>5}: {line_text}",
                    )

                results.append(
                    {
                        "file": rel_path,
                        "line": idx + 1,
                        "context": "\n".join(context_block),
                    },
                )
                total_matches += 1

            if total_matches >= max_results:
                break

        if total_matches >= max_results:
            break

    if not results:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"No matches found for pattern '{pattern}' in "
                    f"{directory} ({files_searched} files searched).",
                ),
            ],
        )

    # Format output
    output_lines = []
    for r in results:
        output_lines.append(f"--- {r['file']}:{r['line']} ---")
        output_lines.append(r["context"])

    truncated_msg = ""
    if total_matches >= max_results:
        truncated_msg = (
            f"\n(Results truncated at {max_results} matches. "
            "Use a more specific pattern or include filter to narrow "
            "down results.)"
        )

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Found {total_matches} matches for pattern "
                f"'{pattern}' in {files_searched} files:\n\n"
                + "\n".join(output_lines)
                + truncated_msg,
            ),
        ],
    )
