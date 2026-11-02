# -*- coding: utf-8 -*-
"""SendFile — send a workspace file to another Discord channel/user."""
from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _DiscordToolBase, _sent


class _SendFileParams(ParamsBase):
    path: str = Field(
        description="Absolute path to the file in your workspace, e.g. one "
        "you just created with Write — the same absolute path you used "
        "there.",
    )
    target_id: str = Field(
        description="Target id, taken verbatim from a ListChats / "
        "ListChatMembers result.",
    )
    target_type: str = Field(
        description="Must match the id: 'channel' for a server channel, "
        "'user' for a person.",
        json_schema_extra={"enum": ["channel", "user"]},
    )


class SendFile(_DiscordToolBase):
    """Send a file to another Discord channel or user."""

    name: str = "SendFile"
    description: str = """Send a file to a Discord channel or person OTHER \
than the current conversation.

## When to Use
- The user asks you to deliver a file (a report, export, ...) to a \
*different* channel or person.

## How to Use
Give ``path`` — a file in your workspace (the one you produced it in). \
Obtain ``target_id`` via ``ListChats`` (channel) or ``ListChatMembers`` \
(person) and pass ``target_id`` + ``target_type`` verbatim. Sending \
requires the user's confirmation.

Any media type rides this same path — Discord renders inline previews for \
common image/video/audio types, so there is no separate image tool. The \
bot upload cap is 8 MiB."""
    is_read_only: bool = False
    input_schema: dict = _SendFileParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        target_id: str,
        target_type: str,
    ) -> ToolChunk:
        """Read ``path`` from the workspace and send it to ``target_id``.

        Args:
            path (`str`): Workspace path of the file to send.
            target_id (`str`): Target id from a discovery result.
            target_type (`str`): ``"channel"`` or ``"user"``.
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
        try:
            await self._channel.send_file_to(
                target_id,
                target_type,
                raw,
                Path(path).name,
            )
        except Exception as e:  # pylint: disable=broad-except
            return _sent(f"file {Path(path).name} to {target_id}", str(e))
        return _sent(f"file {Path(path).name} to {target_id}")
