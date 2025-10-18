# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=line-too-long
"""The view text file tool in agentscope (dangerous; raw OS access).

Warning:
    This helper accesses the host file system directly via ``open`` /
    ``os.path`` without the sandbox guard provided by ``FileDomainService``.
    Register it only when you intentionally need to bypass the logical
    filesystem (e.g. audited access to non-sandbox paths).

Policy:
    Controlled by env var ``AGENTSCOPE_DANGEROUS_TEXT_IO``:

    - ``deny``  : block execution and return a warning message.
    - ``warn``  : default; log a warning and proceed.
    - ``allow`` : proceed silently (still mark ToolResponse.metadata).
"""
import os

from ._write_text_file import _view_text_file
from ._utils import _dangerous_text_io_guard
from .._response import ToolResponse
from ...exception import ToolInvalidArgumentsError
from ...message import TextBlock


async def view_text_file(
    file_path: str,
    ranges: list[int] | None = None,
) -> ToolResponse:
    """View file content with optional line slicing.

    Warning:
        This function bypasses the logical filesystem sandbox and touches
        paths on the host OS directly. Use only after confirming the access
        scope is acceptable for your deployment.

    Args:
        file_path (`str`):
            The target file path.
        ranges:
            The range of lines to be viewed (e.g. lines 1 to 100: [1, 100]), inclusive. If not provided, the entire file will be returned. To view the last 100 lines, use [-100, -1].

    Returns:
        `ToolResponse`:
            The tool response containing the file content or an error message.
    """
    proceed, meta = _dangerous_text_io_guard("view_text_file", file_path)
    if not proceed:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "Blocked by policy: raw OS file access is disabled. "
                        "Use FileSystem service tools instead."
                    ),
                ),
            ],
            metadata=meta,
        )

    if not os.path.exists(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The file {file_path} does not exist.",
                ),
            ],
            metadata=meta,
        )
    if not os.path.isfile(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The path {file_path} is not a file.",
                ),
            ],
            metadata=meta,
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
            metadata=meta,
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
            metadata=meta,
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
            metadata=meta,
        )
