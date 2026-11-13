# -*- coding: utf-8 -*-
"""Send Markdown text to a specified DingTalk user or group."""

from pydantic import Field

from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _DingTalkToolBase


class _SendMessageParams(ParamsBase):
    target: str = Field(
        pattern=r"^(user|group):.+$",
        description="Encoded target returned by ListConversations or "
        "ListUsers.",
    )
    text: str = Field(
        min_length=1,
        description="Markdown-formatted message body.",
    )


class SendMessage(_DingTalkToolBase):
    """Send Markdown text to a conversation other than the current one."""

    name: str = "SendMessage"
    description: str = """Send Markdown text to a DingTalk user or group.

Use this only when the user asks to contact a target other than the current \
conversation. Obtain ``target`` from ``ListConversations`` or ``ListUsers``. \
The operation requires confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(self, target: str, text: str) -> ToolChunk:
        """Send Markdown text to an encoded target.

        Args:
            target (`str`): Encoded DingTalk target.
            text (`str`): Markdown-formatted message body.

        Returns:
            `ToolChunk`: DingTalk acceptance result.
        """
        accepted = await self._channel.send_message_to(target, text)
        return _ack(accepted, f"message to {target}")
