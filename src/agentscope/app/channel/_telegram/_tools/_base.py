# -*- coding: utf-8 -*-
"""Shared Telegram tool behavior."""
from typing import Any, TYPE_CHECKING

from .....message import TextBlock, ToolResultState
from .....permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .....tool import BackendBase, ToolBase, ToolChunk

if TYPE_CHECKING:
    from .._channel import TelegramChannel, _TelegramResult


def _ack(result: "_TelegramResult", what: str) -> ToolChunk:
    """Convert a platform result into a tool result."""
    if result.ok:
        return ToolChunk(
            content=[TextBlock(text=f"Sent {what}.")],
            state=ToolResultState.SUCCESS,
        )
    return ToolChunk(
        content=[
            TextBlock(
                text=f"Failed to send {what}: "
                f"{result.error or 'the platform rejected the request'}",
            ),
        ],
        state=ToolResultState.ERROR,
    )


class _TelegramToolBase(ToolBase):
    """A Telegram tool bound to the live channel and session workspace."""

    is_concurrency_safe: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None
    is_read_only: bool = False

    def __init__(
        self,
        channel: "TelegramChannel",
        backend: BackendBase,
    ) -> None:
        super().__init__()
        self._channel = channel
        self._backend = backend

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Require confirmation before changing Telegram state."""
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Sending content to a Telegram chat requires approval.",
        )
