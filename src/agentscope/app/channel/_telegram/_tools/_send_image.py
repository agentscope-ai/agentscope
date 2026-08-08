# -*- coding: utf-8 -*-
"""SendImage — send a workspace image to a known Telegram chat."""
from pathlib import Path

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _TelegramToolBase


class _SendImageParams(ParamsBase):
    chat_id: str = Field(
        description="A known numeric Telegram chat ID or @channel username.",
    )
    path: str = Field(
        description="The absolute path of an image in the session workspace.",
    )


class SendImage(_TelegramToolBase):
    """Send an inline image from the workspace to a Telegram chat."""

    name: str = "SendImage"
    description: str = """Send a workspace image inline to a known Telegram
chat other than the current conversation. Images are limited to 10 MiB; use
SendFile for a larger image. Sending requires user approval."""
    input_schema: dict = _SendImageParams.model_json_schema()

    async def __call__(self, chat_id: str, path: str) -> ToolChunk:
        try:
            raw = await self._backend.read_file(path)
        except Exception as error:  # pylint: disable=broad-except
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"SendImage could not read {path!r}: {error}",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        result = await self._channel.send_image_to(
            chat_id,
            raw,
            Path(path).name,
        )
        return _ack(result, f"image {Path(path).name} to {chat_id}")
