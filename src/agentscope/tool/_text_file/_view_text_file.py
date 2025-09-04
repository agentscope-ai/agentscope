# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=line-too-long
"""The view text file tool in agentscope."""
import os

from ._write_text_file import _view_text_file
from .._response import ToolResponse
from ...exception import ToolInvalidArgumentsError
from ...message import TextBlock


async def view_text_file(
    file_path: str,
    ranges: list[int] | None = None,
) -> ToolResponse:
    """View the file content in the specified range with line numbers. If `ranges` is not provided, the entire file will be returned.

    Args:
        file_path (`str`):
            The target file path.
        ranges:
            The range of lines to be viewed (e.g. lines 1 to 100: [1, 100]), inclusive. If not provided, the entire file will be returned. To view the last 100 lines, use [-100, -1].

    Returns:
        `ToolResponse`:
            The tool response containing the file content or an error message.
    """
    if not os.path.exists(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The file {file_path} does not exist.",
                ),
            ],
        )
    if not os.path.isfile(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The path {file_path} is not a file.",
                ),
            ],
        )

    try:
        content = _view_text_file(file_path, ranges)
    except ToolInvalidArgumentsError as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=e.message,
                ),
            ],
        )

    if ranges is None:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"""The content of {file_path}:
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
                    text=f"""The content of {file_path} in {ranges} lines:
```
{content}```""",
                ),
            ],
        )
