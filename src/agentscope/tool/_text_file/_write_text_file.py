# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=line-too-long
"""The text file tools in agentscope."""
import os

from ._utils import _calculate_view_ranges, _view_text_file
from .._response import ToolResponse
from ...message import TextBlock


async def insert_text_file(
    file_path: str,
    content: str,
    line_number: int,
) -> ToolResponse:
    """Insert the content at the specified line number in a text file.

    Args:
        file_path (`str`):
            The target file path.
        content (`str`):
            The content to be inserted.
        line_number (`int`):
            The line number at which the content should be inserted, starting
            from 1. If exceeds the number of lines in the file, it will be
            appended to the end of the file.

    Returns:
        `ToolResponse`:
            The tool response containing the result of the insertion operation.
    """
    if line_number <= 0:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"InvalidArgumentsError: "
                    f"The line number {line_number} is invalid. ",
                ),
            ],
        )

    if not os.path.exists(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"InvalidArgumentsError: The target file "
                    f"{file_path} does not exist. ",
                ),
            ],
        )

    with open(file_path, "r", encoding="utf-8") as file:
        original_lines = file.readlines()

    if line_number == len(original_lines) + 1:
        new_lines = original_lines + ["\n" + content]
    elif line_number < len(original_lines) + 1:
        new_lines = (
            original_lines[: line_number - 1]
            + [content + "\n"]
            + original_lines[line_number - 1 :]
        )
    else:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="InvalidArgumentsError: The given line_number "
                    f"({line_number}) is not in the valid range "
                    f"[1, {len(original_lines) + 1}].",
                ),
            ],
        )

    with open(file_path, "w", encoding="utf-8") as file:
        file.writelines(new_lines)

    with open(file_path, "r", encoding="utf-8") as file:
        new_lines = file.readlines()

    start, end = _calculate_view_ranges(
        len(original_lines),
        len(new_lines),
        line_number,
        line_number,
        extra_view_n_lines=5,
    )

    show_content = _view_text_file(file_path, [start, end])

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Insert content into {file_path} at line "
                f"{line_number} successfully. The new content "
                f"between lines {start}-{end} is:\n"
                f"```\n{show_content}```",
            ),
        ],
    )


async def write_text_file(
    file_path: str,
    content: str,
    ranges: None | list[int] = None,
) -> ToolResponse:
    """Create/Replace/Overwrite content in a text file. When `ranges` is provided, the content will be replaced in the specified range. Otherwise, the entire file (if exists) will be overwritten.

    Args:
        file_path (`str`):
            The target file path.
        content (`str`):
            The content to be written.
        ranges (`list[int] | None`, defaults to `None`):
            The range of lines to be replaced. If `None`, the entire file will
            be overwritten.

    Returns:
        `ToolResponse`:
            The tool response containing the result of the writing operation.
    """

    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)

        if ranges:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Create and write {file_path} successfully. "
                        f"The ranges {ranges} is ignored because the "
                        f"file does not exist.",
                    ),
                ],
            )

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Create and write {file_path} successfully.",
                ),
            ],
        )

    with open(file_path, "r", encoding="utf-8") as file:
        original_lines = file.readlines()

    if ranges is not None:
        if (
            isinstance(ranges, list)
            and len(ranges) == 2
            and all(isinstance(i, int) for i in ranges)
        ):
            # Replace content in the specified range
            start, end = ranges
            if start > len(original_lines):
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=f"Error: The start line {start} is invalid. "
                            f"The file only has {len(original_lines)} "
                            f"lines.",
                        ),
                    ],
                )

            new_content = (
                original_lines[: start - 1]
                + [
                    content,
                ]
                + original_lines[end:]
            )

            with open(file_path, "w", encoding="utf-8") as file:
                file.write("".join(new_content))

            # The written content may contain multiple "\n", to avoid mis
            # counting the lines, we read the file again to get the new content
            with open(file_path, "r", encoding="utf-8") as file:
                new_lines = file.readlines()

            view_start, view_end = _calculate_view_ranges(
                len(original_lines),
                len(new_lines),
                start,
                end,
            )

            content = "".join(
                [
                    f"{index + view_start}: {line}"
                    for index, line in enumerate(
                        new_lines[view_start - 1 : view_end],
                    )
                ],
            )

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"""Write {file_path} successfully. The new content snippet:
```
{content}```""",
                    ),
                ],
            )

        else:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error: Invalid range format. Expected a list "
                        f"of two integers, but got {ranges}.",
                    ),
                ],
            )

    with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Overwrite {file_path} successfully.",
            ),
        ],
    )
