# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=line-too-long
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
    """Find files matching a glob pattern under the given directory. Supports recursive patterns with "**" (e.g. "**/*.py" to find all Python files recursively).

    Args:
        pattern (`str`):
            The glob pattern to match files against (e.g. "*.py", "**/*.js", "src/**/*.ts").
        directory (`str`, defaults to `"."`):
            The base directory to search in. Defaults to the current working directory.
        max_results (`int`, defaults to `100`):
            The maximum number of file paths to return.

    Returns:
        `ToolResponse`:
            The tool response containing the matching file paths or an error message.
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

    # Construct the full search pattern
    full_pattern = os.path.join(directory, pattern)

    # Determine if recursive search is needed
    recursive = "**" in pattern

    try:
        matches = glob_module.glob(
            full_pattern,
            recursive=recursive,
        )
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

    # Sort by modification time (most recent first)
    file_matches.sort(key=lambda f: os.path.getmtime(f), reverse=True)

    # Convert to relative paths
    rel_paths = [os.path.relpath(m, directory) for m in file_matches]

    total_found = len(rel_paths)
    truncated = total_found > max_results
    rel_paths = rel_paths[:max_results]

    if not rel_paths:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"No files found matching pattern '{pattern}' "
                    f"in {directory}.",
                ),
            ],
        )

    # Format output with file sizes
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
                text=f"Found {total_found} files matching "
                f"'{pattern}':\n"
                + "\n".join(output_lines)
                + truncated_msg,
            ),
        ],
    )
