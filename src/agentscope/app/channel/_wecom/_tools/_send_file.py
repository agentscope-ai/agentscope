# -*- coding: utf-8 -*-
"""SendFile — upload and send a workspace file to another chat/user."""
from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _WeComToolBase, _ack


class _SendFileParams(ParamsBase):
    path: str = Field(
        description="Absolute path to the file in your workspace, e.g. one "
        "you just created with Write — the same absolute path you used "
        "there.",
    )
    chat_id: str = Field(
        description="A group's chat id, or a person's WeCom userid.",
    )
    chat_type: str = Field(
        description="Must match the id: 'group' for a group chat, "
        "'single' for one person.",
        json_schema_extra={"enum": ["single", "group"]},
    )


class SendFile(_WeComToolBase):
    """Upload and send a file to another WeCom chat/user."""

    name: str = "SendFile"
    description: str = """Send a file to a WeCom chat or person OTHER than \
the current conversation.

## When to Use
- The user asks you to deliver a file (a report, export, ...) to a \
*different* group or person.

## How to Use
Give ``path`` — a file in your workspace (the one you produced it in). \
WeCom exposes no directory lookup, so ``chat_id`` must come from the user \
or from a chat you have already seen. Sending requires the user's \
confirmation.

To send an image so it renders inline, use ``SendImage`` instead."""
    is_read_only: bool = False
    input_schema: dict = _SendFileParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        chat_id: str,
        chat_type: str,
    ) -> ToolChunk:
        """Read ``path`` from the workspace and send it to ``chat_id``.

        Args:
            path (`str`): Workspace path of the file to send.
            chat_id (`str`): Group chat id, or a person's userid.
            chat_type (`str`): ``"single"`` or ``"group"``.
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
        data = await self._channel.send_file_to(
            chat_id,
            chat_type,
            raw,
            Path(path).name,
        )
        return _ack(data, f"file {Path(path).name} to {chat_id}")
