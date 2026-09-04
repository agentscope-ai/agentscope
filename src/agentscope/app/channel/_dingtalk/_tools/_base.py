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


def _failure(message: str) -> ToolChunk:
    """Return a visible, non-successful DingTalk tool result.

    Args:
        message (`str`): User-actionable failure description.

    Returns:
        `ToolChunk`: Error result for the agent.
    """
    return ToolChunk(
        content=[TextBlock(text=message)],
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


class _DingTalkKnowledgeToolBase(_DingTalkToolBase):
    """Read-only DingTalk knowledge tool bound to one trusted sender."""

    is_read_only: bool = True

    def __init__(
        self,
        channel: "DingTalkChannel",
        backend: BackendBase,
        channel_user_id: str,
    ) -> None:
        """Bind the tool to the server-supplied current channel user.

        Args:
            channel (`DingTalkChannel`): Live DingTalk channel.
            backend (`BackendBase`): Calling session workspace backend.
            channel_user_id (`str`): Trusted staff id from the inbound event.
        """
        super().__init__(channel, backend)
        self._channel_user_id = channel_user_id
