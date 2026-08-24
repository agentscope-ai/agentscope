# -*- coding: utf-8 -*-
"""Send a workspace file to a specified DingTalk user or group."""

from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _DingTalkToolBase


class _SendFileParams(ParamsBase):
    path: str = Field(
        description="Absolute path to a file in the calling workspace.",
    )
    target: str | None = Field(
        default=None,
        pattern=r"^(user|group):.+$",
        description="Encoded target from ListConversations or ListUsers. "
        "Omit to send to the current conversation.",
    )


class SendFile(_DingTalkToolBase):
    """Send a supported workspace file to a DingTalk target."""

    name: str = "SendFile"
    description: str = """Send a workspace file to a specified DingTalk user \
or group.

Supported DingTalk file extensions are doc, docx, pdf, rar, xlsx, and zip. \
Omit ``target`` to send to the current conversation, or take another \
conversation's ``target`` from a discovery tool. The operation requires \
confirmation. Use ``SendImage`` for inline images."""
    is_read_only: bool = False
    input_schema: dict = _SendFileParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        target: str | None = None,
    ) -> ToolChunk:
        """Read a workspace file and send it, defaulting to this chat.

        Args:
            path (`str`): Workspace file path.
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
