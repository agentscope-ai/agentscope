# -*- coding: utf-8 -*-
"""SendFile — upload and send a workspace file to another channel/user."""
from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _DiscordToolBase, _ack


class _SendFileParams(ParamsBase):
    path: str = Field(
        description="Absolute path to the file in your workspace, e.g. one "
        "you just created with Write — the same absolute path you used "
        "there.",
    )
    target_id: str = Field(
        description="Target id, taken verbatim from a ListChats "
        "(channel) or ListChatMembers (user) result.",
    )
    target: str = Field(
        description="Must match the id: 'channel' for a server channel "
        "or DM channel, 'user' for a person.",
        json_schema_extra={"enum": ["channel", "user"]},
    )


class SendFile(_DiscordToolBase):
    """Upload and send a file to another Discord channel/user."""

    name: str = "SendFile"
    description: str = """Send a file to a Discord chat or person OTHER than \
the current conversation.

## When to Use
- The user asks you to deliver a file (a report, export, ...) to a \
*different* channel or person.

## How to Use
Give ``path`` — a file in your workspace (the one you produced it in). \
Obtain ``target_id`` via ``ListChats`` (channel) or ``ListChatMembers`` \
(person) and pass ``target_id`` + ``target`` verbatim. Sending requires \
the user's confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendFileParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        target_id: str,
        target: str,
    ) -> ToolChunk:
        """Read ``path`` from the workspace and send it to ``target_id``.

        Args:
            path (`str`): Workspace path of the file to send.
            target_id (`str`): Target id from a discovery result.
            target (`str`): ``"channel"`` or ``"user"``.
        """
        try:
            raw = await self._backend.read_file(path)
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[
                    TextBlock(text=f"SendFile: cannot read {path!r}: {e}"),
                ],
                state=ToolResultState.ERROR,
            )
        ok, error = await self._channel.send_file_to(
            target_id,
            target,
            raw,
            Path(path).name,
        )
        return _ack(ok, f"file {Path(path).name} to {target_id}", error)
