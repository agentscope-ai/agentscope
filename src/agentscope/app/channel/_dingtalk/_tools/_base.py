# -*- coding: utf-8 -*-
"""Shared base and result helper for DingTalk agent tools."""

from typing import Any, TYPE_CHECKING

from .....message import TextBlock, ToolResultState
from .....permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .....tool import BackendBase, ToolBase, ToolChunk

if TYPE_CHECKING:
    from .._channel import DingTalkChannel


def _ack(accepted: bool, what: str) -> ToolChunk:
    """Convert a DingTalk send result into a tool chunk.

    Args:
        accepted (`bool`): Whether DingTalk accepted the request.
        what (`str`): Short description of the attempted operation.

    Returns:
        `ToolChunk`: Success or error result for the agent.
    """
    if accepted:
        return ToolChunk(content=[TextBlock(text=f"Sent {what}.")])
    return ToolChunk(
        content=[
            TextBlock(
                text=f"Failed to send {what}: DingTalk rejected the request.",
            ),
        ],
        state=ToolResultState.ERROR,
    )


class _DingTalkToolBase(ToolBase):
    """Base for DingTalk tools bound to a channel and workspace."""

    is_concurrency_safe: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        channel: "DingTalkChannel",
        backend: BackendBase,
    ) -> None:
        """Bind the live channel and session workspace backend.

        Args:
            channel (`DingTalkChannel`): Live DingTalk channel.
            backend (`BackendBase`): Workspace backend for file reads.
        """
        super().__init__()
        self._channel = channel
        self._backend = backend

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow lookups and ask before cross-target sends.

        Args:
            tool_input (`dict[str, Any]`): Proposed tool arguments.
            context (`PermissionContext`): Session permission context.

        Returns:
            `PermissionDecision`: Read allow or send confirmation request.
        """
        del tool_input, context
        if self.is_read_only:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"{self.name} is a read-only lookup.",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Sending to another DingTalk conversation needs the "
            "user's confirmation.",
        )
