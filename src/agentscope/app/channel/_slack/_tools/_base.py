# -*- coding: utf-8 -*-
"""Shared base and reply helper for the Slack agent tools."""
from typing import Any, TYPE_CHECKING

from .....message import TextBlock, ToolResultState
from .....permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .....tool import BackendBase, ToolBase, ToolChunk

if TYPE_CHECKING:
    from .._channel import SlackChannel


def _ack(data: dict | None, what: str) -> ToolChunk:
    """Turn a Slack send result into a success/error chunk.

    A message long enough to be split reports how many parts went out, and
    a failure part-way through says how much of it landed, so the agent
    never reads a partial delivery as a whole one.

    Args:
        data (`dict | None`): The channel's ``{"ok": ...}`` result.
        what (`str`): Short label of what was sent, for the message.
    """
    result = data or {}
    segments = result.get("segments") or 0
    if result.get("ok"):
        suffix = f" in {segments} messages" if segments > 1 else ""
        return ToolChunk(content=[TextBlock(text=f"Sent {what}{suffix}.")])
    msg = result.get("error") or "the platform rejected the request"
    sent = len(result.get("sent_ts") or [])
    progress = f" after {sent} of {segments} messages" if segments > 1 else ""
    return ToolChunk(
        content=[TextBlock(text=f"Failed to send {what}{progress}: {msg}")],
        state=ToolResultState.ERROR,
    )


class _SlackToolBase(ToolBase):
    """Base for every Slack tool: holds the channel and the session
    workspace, and asks before any send to another conversation/user."""

    is_concurrency_safe: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        channel: "SlackChannel",
        backend: BackendBase,
    ) -> None:
        """Bind the live channel and the session's workspace backend.

        Args:
            channel (`SlackChannel`): The live channel to send / query.
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
        """Allow reads; ask before sending to another conversation/user.

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
            message="Sending to another Slack conversation/user needs the "
            "user's confirmation.",
        )
