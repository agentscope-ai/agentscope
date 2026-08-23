# -*- coding: utf-8 -*-
"""SendImage — upload and send a workspace image, rendered inline."""
from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _FeishuToolBase, _ack


class _SendImageParams(ParamsBase):
    path: str = Field(
        description="Absolute path to the image file in your workspace — "
        "the same absolute path you used to create it.",
    )
    receive_id: str = Field(
        description="Target id, taken verbatim from a ListChats / "
        "ListChatMembers result.",
    )
    receive_id_type: str = Field(
        description="Must match the id: 'chat_id' for a group, 'open_id' "
        "for a person.",
        json_schema_extra={"enum": ["chat_id", "open_id"]},
    )


class SendImage(_FeishuToolBase):
    """Upload and send an image to another Feishu chat/user."""

    name: str = "SendImage"
    description: str = """Send an image to a Feishu chat or person OTHER \
than the current conversation, rendered inline.

## When to Use
- The user asks you to send a picture/chart to a *different* group or \
person, and you want it shown inline (not as a file attachment).

## How to Use
Give ``path`` to the image file. Obtain ``receive_id`` via ``ListChats`` \
(group) or ``ListChatMembers`` (person) and pass ``receive_id`` + \
``receive_id_type`` verbatim. Sending requires the user's confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendImageParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        receive_id: str,
        receive_id_type: str,
    ) -> ToolChunk:
        """Read the image at ``path`` from the workspace and send it.

        Args:
            path (`str`): Workspace path of the image to send.
            receive_id (`str`): Target id from a discovery result.
            receive_id_type (`str`): ``"chat_id"`` or ``"open_id"``.
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
        data = await self._channel.send_image_to(
            receive_id,
            receive_id_type,
            raw,
        )
        return _ack(data, f"image to {receive_id}")
