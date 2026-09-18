# -*- coding: utf-8 -*-
"""SendFile — send a workspace file to a Discord channel or user."""

from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _DiscordToolBase


class _SendFileParams(ParamsBase):
    path: str = Field(
        description="Absolute path to a file in the calling workspace.",
    )
    target: str = Field(
        pattern=r"^(channel|user):\d+$",
        description="Target returned by ListChats or ListChatMembers.",
    )


class SendFile(_DiscordToolBase):
    """Send one workspace file to a Discord channel or user."""

    name: str = "SendFile"
    description: str = """Send a workspace file to a Discord guild channel \
or user DM other than the current conversation.

Obtain ``target`` from a discovery tool. The file is read from the current \
session workspace, not the host filesystem. Images use this same file-send \
path. This cross-target send requires user confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendFileParams.model_json_schema()

    async def __call__(self, path: str, target: str) -> ToolChunk:
        """Read one workspace file and send it to a Discord target.

        Args:
            path (`str`): Workspace file path.
            target (`str`): Encoded Discord channel or user target.

        Returns:
            `ToolChunk`: Discord acceptance or workspace error.
        """
        try:
            raw = await self._backend.read_file(path)
        except Exception as error:  # pylint: disable=broad-except
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"SendFile: cannot read {path!r}: {error}",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        file_name = Path(path).name
        accepted = await self._channel.send_file_to(
            target,
            raw,
            file_name,
        )
        return _ack(accepted, f"file {file_name} to {target}")
