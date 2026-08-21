# -*- coding: utf-8 -*-
"""Shared base and reply helper for the Discord agent tools."""
from typing import Any, TYPE_CHECKING

from .....message import TextBlock, ToolResultState
from .....permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .....tool import BackendBase, ToolBase, ToolChunk

if TYPE_CHECKING:
    from .._channel import DiscordChannel


def _ack(ok: bool, what: str, error: str = "") -> ToolChunk:
    """Turn a Discord send outcome into a success/error chunk.

    Args:
        ok (`bool`): Whether the send succeeded.
        what (`str`): Short label of what was sent, for the message.
        error (`str`): The failure reason, if any.
    """
    if ok:
        return ToolChunk(content=[TextBlock(text=f"Sent {what}.")])
    detail = error or "the platform rejected the request"
    return ToolChunk(
        content=[TextBlock(text=f"Failed to send {what}: {detail}")],
        state=ToolResultState.ERROR,
    )


class _DiscordToolBase(ToolBase):
    """Base for every Discord tool: holds the channel and the session
    workspace, and asks before any send to another chat/user."""

    is_concurrency_safe: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        channel: "DiscordChannel",
        backend: BackendBase,
    ) -> None:
        """Bind the live channel and the session's workspace backend.

        Args:
            channel (`DiscordChannel`): The live channel to send / query.
            backend (`BackendBase`): The session workspace backend; the
                file tools read their payload from it (others ignore it).
        """
        super().__init__()
        self._channel = channel
        self._backend = backend

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow reads; ask before sending to another chat/user.

        Args:
            tool_input (`dict[str, Any]`): The proposed tool call args.
            context (`PermissionContext`): The session permission context.
        """
        if self.is_read_only:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"{self.name} is a read-only lookup.",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Sending to another Discord chat/user needs the user's "
            "confirmation.",
        )
