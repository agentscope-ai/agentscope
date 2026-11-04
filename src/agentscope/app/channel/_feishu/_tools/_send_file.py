# -*- coding: utf-8 -*-
"""SendFile — upload and send a workspace file to another chat/user."""
from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _FeishuToolBase, _ack


class _SendFileParams(ParamsBase):
    path: str = Field(
        description="Absolute path to the file in your workspace, e.g. one "
        "you just created with Write — the same absolute path you used "
        "there.",
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


class SendFile(_FeishuToolBase):
    """Upload and send a file to another Feishu chat/user."""

    name: str = "SendFile"
    description: str = """Send a file to a Feishu chat or person OTHER than \
the current conversation.

## When to Use
- The user asks you to deliver a file (a report, export, ...) to a \
*different* group or person.

## How to Use
Give ``path`` — a file in your workspace (the one you produced it in). \
Obtain ``receive_id`` via ``ListChats`` (group) or ``ListChatMembers`` \
(person) and pass ``receive_id`` + ``receive_id_type`` verbatim. Sending \
requires the user's confirmation.

To send an image so it renders inline, use ``SendImage`` instead."""
    is_read_only: bool = False
    input_schema: dict = _SendFileParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        receive_id: str,
        receive_id_type: str,
    ) -> ToolChunk:
        """Read ``path`` from the workspace and send it to ``receive_id``.

        Args:
            path (`str`): Workspace path of the file to send.
            receive_id (`str`): Target id from a discovery result.
            receive_id_type (`str`): ``"chat_id"`` or ``"open_id"``.
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
            receive_id,
            receive_id_type,
            raw,
            Path(path).name,
        )
        return _ack(data, f"file {Path(path).name} to {receive_id}")
