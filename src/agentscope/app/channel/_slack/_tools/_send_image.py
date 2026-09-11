# -*- coding: utf-8 -*-
"""SendImage — upload a workspace image, which Slack renders inline."""
from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _SlackToolBase, _ack


class _SendImageParams(ParamsBase):
    path: str = Field(
        description="Absolute path to the image file in your workspace — "
        "the same absolute path you used to create it.",
    )
    chat_id: str = Field(
        description="Target id, taken verbatim from a ListChats / "
        "ListChatMembers result. A user id opens a direct message.",
    )


class SendImage(_SlackToolBase):
    """Upload and send an image to another Slack conversation/user."""

    name: str = "SendImage"
    description: str = """Send an image to a Slack channel or person OTHER \
than the current conversation. Slack previews it inline.

## When to Use
- The user asks you to send a picture/chart to a *different* channel or \
person.

## How to Use
Give ``path`` to the image file. Obtain ``chat_id`` via ``ListChats`` \
(channel) or ``ListChatMembers`` (person). Sending requires the user's \
confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendImageParams.model_json_schema()

    async def __call__(self, path: str, chat_id: str) -> ToolChunk:
        """Read the image at ``path`` from the workspace and send it.

        Args:
            path (`str`): Workspace path of the image to send.
            chat_id (`str`): Target id from a discovery result.
        """
        try:
            raw = await self._backend.read_file(path)
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[
                    TextBlock(text=f"SendImage: cannot read {path!r}: {e}"),
                ],
                state=ToolResultState.ERROR,
            )
        data = await self._channel.upload_file(chat_id, raw, Path(path).name)
        return _ack(data, f"image to {chat_id}")
