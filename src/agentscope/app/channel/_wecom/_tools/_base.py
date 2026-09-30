# -*- coding: utf-8 -*-
"""Shared base and reply helper for the WeCom agent tools."""
from typing import Any, TYPE_CHECKING

from .....message import TextBlock, ToolResultState
from .....permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .....tool import BackendBase, ToolBase, ToolChunk

if TYPE_CHECKING:
    from .._channel import WeComChannel


def _ack(data: dict | None, what: str) -> ToolChunk:
    """Turn a WeCom send ack into a success/error chunk.

    Args:
        data (`dict | None`): The platform ack frame.
        what (`str`): Short label of what was sent, for the message.
    """
    if data and data.get("errcode") == 0:
        return ToolChunk(content=[TextBlock(text=f"Sent {what}.")])
    msg = (data or {}).get("errmsg") or "the platform rejected the request"
    return ToolChunk(
        content=[TextBlock(text=f"Failed to send {what}: {msg}")],
        state=ToolResultState.ERROR,
    )


class _WeComToolBase(ToolBase):
    """Base for every WeCom tool: holds the channel and the session
    workspace, and asks before any send to another chat/user."""

    is_concurrency_safe: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        channel: "WeComChannel",
        backend: BackendBase,
    ) -> None:
        """Bind the live channel and the session's workspace backend.

        Args:
            channel (`WeComChannel`): The live channel to send through.
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
        """Ask before sending to another chat/user.

        Args:
            tool_input (`dict[str, Any]`): The proposed tool call args.
            context (`PermissionContext`): The session permission context.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Sending to another WeCom chat/user needs the user's "
            "confirmation.",
        )
