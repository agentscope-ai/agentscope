# -*- coding: utf-8 -*-
"""Send a workspace image to a specified DingTalk user or group."""

from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _DingTalkToolBase


class _SendImageParams(ParamsBase):
    path: str = Field(
        description="Absolute path to an image in the calling workspace.",
    )
    target: str | None = Field(
        default=None,
        pattern=r"^(user|group):.+$",
        description="Encoded target from ListConversations or ListUsers. "
        "Omit to send to the current conversation.",
    )


class SendImage(_DingTalkToolBase):
    """Send a workspace image inline to a DingTalk target."""

    name: str = "SendImage"
    description: str = """Send a workspace image to a specified DingTalk user \
or group so it renders inline.

Omit ``target`` to send to the current conversation, or take another \
conversation's ``target`` from ``ListConversations`` or ``ListUsers``. The \
operation requires confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendImageParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        target: str | None = None,
    ) -> ToolChunk:
        """Read a workspace image and send it, defaulting to this chat.

        Args:
            path (`str`): Workspace image path.
            target (`str | None`): Encoded DingTalk target; the current
                conversation when omitted.

        Returns:
            `ToolChunk`: DingTalk acceptance or workspace error.
        """
        target = target or self._chat_id
        try:
            raw = await self._backend.read_file(path)
        except Exception as error:  # pylint: disable=broad-except
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"SendImage: cannot read {path!r}: {error}",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        file_name = Path(path).name
        accepted = await self._channel.send_image_to(
            target,
            raw,
            file_name,
        )
        return _ack(accepted, f"image to {target}")
