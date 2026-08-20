# -*- coding: utf-8 -*-
"""SendFile — send a workspace file to a known Telegram chat."""
from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _TelegramToolBase


class _SendFileParams(ParamsBase):
    chat_id: str = Field(
        description="A known numeric Telegram chat ID or @channel username.",
    )
    path: str = Field(
        description="The absolute path of a file in the session workspace.",
    )


class SendFile(_TelegramToolBase):
    """Send a file from the session workspace to a Telegram chat."""

    name: str = "SendFile"
    description: str = """Send a workspace file to a known Telegram chat.

Use this only for a chat other than the current conversation. ``path`` must
refer to a file in the session workspace. Files are limited to 50 MiB, and
a private user must have started the bot before receiving one. Sending
requires user approval."""
    input_schema: dict = _SendFileParams.model_json_schema()

    async def __call__(self, chat_id: str, path: str) -> ToolChunk:
        try:
            raw = await self._backend.read_file(path)
        except Exception as error:  # pylint: disable=broad-except
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"SendFile could not read {path!r}: {error}",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        result = await self._channel.send_file_to(
            chat_id,
            raw,
            Path(path).name,
        )
        return _ack(result, f"file {Path(path).name} to {chat_id}")
