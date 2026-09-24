# -*- coding: utf-8 -*-
"""Shared base for the read-only wiki tools."""

from typing import Any, TYPE_CHECKING

from ....permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ....tool import ToolBase

if TYPE_CHECKING:
    from .._base import ChannelBase


class _WikiToolBase(ToolBase):
    """A wiki lookup bound to the one platform user it reads as."""

    is_read_only: bool = True
    is_concurrency_safe: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        channel: "ChannelBase",
        channel_user_id: str,
    ) -> None:
        """Bind the live channel and the user whose access is used.

        Args:
            channel (`ChannelBase`): The channel serving this session.
            channel_user_id (`str`): The platform user to read as. Supplied
                by the server from the inbound message, never by the model.
        """
        super().__init__()
        self._channel = channel
        self._channel_user_id = channel_user_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow the lookup: the platform already scopes it to the user.

        Args:
            tool_input (`dict[str, Any]`): Proposed tool arguments.
            context (`PermissionContext`): Session permission context.

        Returns:
            `PermissionDecision`: Always allow.
        """
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is a read-only lookup.",
        )
