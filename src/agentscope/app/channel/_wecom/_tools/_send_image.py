# -*- coding: utf-8 -*-
"""SendImage — upload and send a workspace image, rendered inline."""
from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _WeComToolBase, _ack


class _SendImageParams(ParamsBase):
    path: str = Field(
        description="Absolute path to the image file in your workspace — "
        "the same absolute path you used to create it.",
    )
    chat_id: str = Field(
        description="A group's chat id, or a person's WeCom userid.",
    )
    chat_type: str = Field(
        description="Must match the id: 'group' for a group chat, "
        "'single' for one person.",
        json_schema_extra={"enum": ["single", "group"]},
    )


class SendImage(_WeComToolBase):
    """Upload and send an image to another WeCom chat/user."""

    name: str = "SendImage"
    description: str = """Send an image to a WeCom chat or person OTHER \
than the current conversation, rendered inline.

## When to Use
- The user asks you to send a picture/chart to a *different* group or \
person, and you want it shown inline (not as a file attachment).

## How to Use
Give ``path`` to the image file. WeCom exposes no directory lookup, so \
``chat_id`` must come from the user or from a chat you have already seen. \
Sending requires the user's confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendImageParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        chat_id: str,
        chat_type: str,
    ) -> ToolChunk:
        """Read the image at ``path`` from the workspace and send it.

        Args:
            path (`str`): Workspace path of the image to send.
            chat_id (`str`): Group chat id, or a person's userid.
            chat_type (`str`): ``"single"`` or ``"group"``.
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
        data = await self._channel.send_image_to(chat_id, chat_type, raw)
        return _ack(data, f"image to {chat_id}")
