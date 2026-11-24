# -*- coding: utf-8 -*-
"""Shared output handling for the builtin shell tools."""

#: Maximum number of characters a builtin shell tool keeps in its
#: returned output before truncating it.
OUTPUT_TRUNCATION_LIMIT = 30000


def truncate_output(
    output: str,
    limit: int = OUTPUT_TRUNCATION_LIMIT,
) -> str:
    """Truncate long tool output while keeping the cut transparent.

    The returned text keeps the first ``limit`` characters and appends a
    notice that states the original output length, how many characters
    were omitted, and how to retrieve the rest, so callers never mistake
    the head of an output for the whole output.

    Args:
        output (`str`):
            The full command output to truncate.
        limit (`int`, defaults to `OUTPUT_TRUNCATION_LIMIT`):
            The maximum number of characters to keep.

    Returns:
        `str`:
            The original output when it fits within ``limit``, otherwise
            the first ``limit`` characters followed by the notice.
    """
    if len(output) <= limit:
        return output
    omitted = len(output) - limit
    return (
        f"{output[:limit]}\n... (output truncated: showing the first "
        f"{limit} of {len(output)} characters; {omitted} characters "
        f"omitted. To inspect the rest, re-run the command with its "
        f"output redirected to a file, or narrow the output.)"
    )
