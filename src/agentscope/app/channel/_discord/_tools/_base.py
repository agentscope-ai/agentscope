# -*- coding: utf-8 -*-
"""Shared base for the Discord agent tools."""
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


def _sent(what: str, error: str | None = None) -> ToolChunk:
    """Turn a Discord send outcome into a success/error chunk.

    Args:
        what (`str`): Short label of what was sent, for the message.
        error (`str | None`): The failure reason, or ``None`` on success.
    """
    if error is None:
        return ToolChunk(content=[TextBlock(text=f"Sent {what}.")])
    return ToolChunk(
        content=[TextBlock(text=f"Failed to send {what}: {error}")],
        state=ToolResultState.ERROR,
    )


class _DiscordToolBase(ToolBase):
    """Base for every Discord tool: holds the channel and the session
    workspace, and asks before any send to another channel/user."""

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
        """Allow reads; ask before sending to another channel/user.

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
            message="Sending to another Discord channel/user needs the "
            "user's confirmation.",
        )
