# -*- coding: utf-8 -*-
"""The utility functions for text file tools in agentscope."""
from ...exception import ToolInvalidArgumentsError


def _validate_path(file_path: str) -> str:
    """Validate the file path to prevent path traversal attacks.

    The function ensures the resolved path stays within the current working
    directory or any explicitly allowed absolute base directory set via the
    ``AGENTSCOPE_WORKSPACE`` environment variable.

    Args:
        file_path (`str`):
            The file path to validate. Absolute paths are rejected unless the
            path is contained within ``AGENTSCOPE_WORKSPACE``.

    Returns:
        `str`:
            The validated, normalised file path string.

    Raises:
        ToolInvalidArgumentsError: If the path is an absolute path outside
            the allowed workspace, or if path traversal is detected.
    """
    import os
    from pathlib import Path

    workspace_env = os.environ.get("AGENTSCOPE_WORKSPACE", "./workspace")
    if workspace_env:
        base = Path(workspace_env).resolve()
    else:
        base = Path.cwd()

    # Reject absolute paths that attempt to bypass the workspace
    if os.path.isabs(file_path):
        resolved = Path(file_path).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ToolInvalidArgumentsError(
                f"SecurityError: Absolute path '{file_path}' is outside "
                f"the allowed workspace '{base}'.",
            ) from exc
        return str(resolved)

    # For relative paths, resolve against base and check containment
    resolved = (base / file_path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ToolInvalidArgumentsError(
            f"SecurityError: Path traversal detected in '{file_path}'. "
            f"File operations are restricted to '{base}'.",
        ) from exc
    return str(resolved)


def _calculate_view_ranges(
    old_n_lines: int,
    new_n_lines: int,
    start: int,
    end: int,
    extra_view_n_lines: int = 5,
) -> tuple[int, int]:
    """Calculate after writing the new content, the view ranges of the file.

    Args:
        old_n_lines (`int`):
            The number of lines before writing the new content.
        new_n_lines (`int`):
            The number of lines after writing the new content.
        start (`int`):
            The start line of the writing range.
        end (`int`):
            The end line of the writing range.
        extra_view_n_lines (`int`, optional):
            The number of extra lines to view before and after the range.
    """

    view_start = max(1, start - extra_view_n_lines)

    delta_lines = new_n_lines - old_n_lines
    view_end = min(end + delta_lines + extra_view_n_lines, new_n_lines)

    return view_start, view_end


def _assert_ranges(
    ranges: list[int],
) -> None:
    """Check if the ranges are valid.

    Raises:
        ToolInvalidArgumentsError: If the ranges are invalid.
    """
    if (
        isinstance(ranges, list)
        and len(ranges) == 2
        and all(isinstance(i, int) for i in ranges)
    ):
        start, end = ranges
        if start > end:
            raise ToolInvalidArgumentsError(
                f"InvalidArgumentError: The start line is greater than the "
                f"end line in the given range {ranges}.",
            )
    else:
        raise ToolInvalidArgumentsError(
            f"InvalidArgumentError: Invalid range format. Expected a list of "
            f"two integers, but got {ranges}.",
        )


def _view_text_file(
    file_path: str,
    ranges: list[int] | None = None,
) -> str:
    """Return the file content in the specified range with line numbers."""
    with open(file_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    if ranges:
        _assert_ranges(ranges)
        start, end = ranges

        if start > len(lines):
            raise ToolInvalidArgumentsError(
                f"InvalidArgumentError: The range '{ranges}' is out of bounds "
                f"for the file '{file_path}', which has only {len(lines)} "
                f"lines.",
            )

        view_content = [
            f"{index + start}: {line}"
            for index, line in enumerate(lines[start - 1 : end])
        ]

        return "".join(view_content)

    return "".join(f"{index + 1}: {line}" for index, line in enumerate(lines))
