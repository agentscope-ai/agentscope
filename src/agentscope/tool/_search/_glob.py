# -*- coding: utf-8 -*-
"""The glob search tool in agentscope."""
import glob as glob_module
import os

from .._response import ToolResponse
from ...message import TextBlock


async def glob_search(
    pattern: str,
    directory: str = ".",
    max_results: int = 100,
) -> ToolResponse:
    """Find files matching a glob pattern under the given directory.
    Supports recursive patterns with "**" (e.g. "**/*.py" to find all
    Python files recursively).

    Args:
        pattern (`str`):
            The glob pattern to match files against (e.g. "*.py",
            "**/*.js", "src/**/*.ts"). Must be a relative pattern —
            absolute patterns and traversal sequences ("..") are
            rejected.
        directory (`str`, defaults to `"."`):
            The base directory to search in. Defaults to the current
            working directory.
        max_results (`int`, defaults to `100`):
            The maximum number of file paths to return.

    Returns:
        `ToolResponse`:
            The tool response containing the matching file paths or an
            error message.
    """
    # Reject absolute patterns and traversal sequences to prevent
    # escaping the base directory.
    if os.path.isabs(pattern):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Error: pattern must be relative, not absolute.",
                ),
            ],
        )

    normalized = os.path.normpath(pattern)
    if normalized.startswith(".."):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Error: pattern must not traverse outside the base directory.",
                ),
            ],
        )

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

    full_pattern = os.path.join(directory, pattern)
    recursive = "**" in pattern

    try:
        matches = glob_module.glob(full_pattern, recursive=recursive)
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Failed to execute glob pattern: {e}",
                ),
            ],
        )

    # Filter out directories — only return files
    file_matches = [m for m in matches if os.path.isfile(m)]

    # Sort by modification time (most recent first); treat inaccessible
    # files as oldest so they sort to the end rather than crashing.
    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    file_matches.sort(key=_mtime, reverse=True)

    rel_paths = [os.path.relpath(m, directory) for m in file_matches]

    total_found = len(rel_paths)
    truncated = total_found > max_results
    rel_paths = rel_paths[:max_results]

    if not rel_paths:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"No files found matching pattern '{pattern}' in {directory}.",
                ),
            ],
        )

    output_lines = []
    for path in rel_paths:
        full_path = os.path.join(directory, path)
        try:
            size = os.path.getsize(full_path)
            if size < 1024:
                size_str = f"{size}B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f}MB"
        except OSError:
            size_str = "?"

        output_lines.append(f"  {path} ({size_str})")

    truncated_msg = ""
    if truncated:
        truncated_msg = (
            f"\n(Showing {max_results} of {total_found} matches. "
            "Use a more specific pattern to narrow down results.)"
        )

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=(
                    f"Found {total_found} files matching '{pattern}':\n"
                    + "\n".join(output_lines)
                    + truncated_msg
                ),
            ),
        ],
    )
