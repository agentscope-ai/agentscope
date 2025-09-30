# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""The shell command tool in agentscope."""

import asyncio
from typing import Any

from .._response import ToolResponse
from ...message import TextBlock


async def execute_shell_command(
    command: str,
    timeout: int = 300,
    **kwargs: Any,
) -> ToolResponse:
    """Execute given command and return the return code, standard output and
    error within <returncode></returncode>, <stdout></stdout> and
    <stderr></stderr> tags.

    Args:
        command (`str`):
            The shell command to execute.
        timeout (`float`, defaults to `300`):
            The maximum time (in seconds) allowed for the command to run.

    Returns:
        `ToolResponse`:
            The tool response containing the return code, standard output, and
            standard error of the executed command.
    """

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        bufsize=0,
    )

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode("utf-8")
        stderr_str = stderr.decode("utf-8")
        returncode = proc.returncode

    except asyncio.TimeoutError:
        stderr_suffix = (
            f"TimeoutError: The command execution exceeded "
            f"the timeout of {timeout} seconds."
        )
        returncode = -1
        try:
            proc.terminate()
            stdout, stderr = await proc.communicate()
            stdout_str = stdout.decode("utf-8")
            stderr_str = stderr.decode("utf-8")
            if stderr_str:
                stderr_str += f"\n{stderr_suffix}"
            else:
                stderr_str = stderr_suffix
        except ProcessLookupError:
            stdout_str = ""
            stderr_str = stderr_suffix

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=(
                    f"<returncode>{returncode}</returncode>"
                    f"<stdout>{stdout_str}</stdout>"
                    f"<stderr>{stderr_str}</stderr>"
                ),
            ),
        ],
    )
