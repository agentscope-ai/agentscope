# -*- coding: utf-8 -*-
"""SendFile — upload a workspace file to another conversation/user."""
from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _SlackToolBase, _ack


class _SendFileParams(ParamsBase):
    path: str = Field(
        description="Absolute path to the file in your workspace, e.g. one "
        "you just created with Write — the same absolute path you used "
        "there.",
    )
    chat_id: str = Field(
        description="Target id, taken verbatim from a ListChats / "
        "ListChatMembers result. A user id opens a direct message.",
    )


class SendFile(_SlackToolBase):
    """Upload and send a file to another Slack conversation/user."""

    name: str = "SendFile"
    description: str = """Send a file to a Slack channel or person OTHER \
than the current conversation.

## When to Use
- The user asks you to deliver a file (a report, export, ...) to a \
*different* channel or person.

## How to Use
Give ``path`` — a file in your workspace (the one you produced it in). \
Obtain ``chat_id`` via ``ListChats`` (channel) or ``ListChatMembers`` \
(person). Sending requires the user's confirmation.

For an image you want shown inline, use ``SendImage`` instead."""
    is_read_only: bool = False
    input_schema: dict = _SendFileParams.model_json_schema()

    async def __call__(self, path: str, chat_id: str) -> ToolChunk:
        """Read ``path`` from the workspace and send it to ``chat_id``.

        Args:
            path (`str`): Workspace path of the file to send.
            chat_id (`str`): Target id from a discovery result.
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
        data = await self._channel.upload_file(
            chat_id,
            raw,
            Path(path).name,
        )
        return _ack(data, f"file {Path(path).name} to {chat_id}")
