# -*- coding: utf-8 -*-
"""Send Markdown text to a specified DingTalk user or group."""

from pydantic import Field

from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _DingTalkToolBase


class _SendMessageParams(ParamsBase):
    target: str | None = Field(
        default=None,
        pattern=r"^(user|group):.+$",
        description="Encoded target from ListConversations or ListUsers. "
        "Omit to send to the current conversation.",
    )
    text: str = Field(
        min_length=1,
        description="Markdown-formatted message body.",
    )


class SendMessage(_DingTalkToolBase):
    """Send Markdown text to the current or another DingTalk conversation."""

    name: str = "SendMessage"
    description: str = """Send Markdown text to a DingTalk user or group.

Omit ``target`` to send to the current conversation. Name another \
conversation only when the user asks to reach one, taking its ``target`` \
from ``ListConversations`` or ``ListUsers``. The operation requires \
confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(
        self,
        text: str,
        target: str | None = None,
    ) -> ToolChunk:
        """Send Markdown text to a target, defaulting to this chat.

        Args:
            text (`str`): Markdown-formatted message body.
            target (`str | None`): Encoded DingTalk target; the current
                conversation when omitted.

        Returns:
            `ToolChunk`: DingTalk acceptance result.
        """
        target = target or self._chat_id
        accepted = await self._channel.send_message_to(target, text)
        return _ack(accepted, f"message to {target}")
