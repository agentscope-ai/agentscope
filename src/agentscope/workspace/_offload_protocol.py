# -*- coding: utf-8 -*-
"""The offload protocol."""
from typing import Protocol, Any

from ..message import Msg, ToolResultBlock


class Offloader(Protocol):
    """The offloader protocol."""

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> str:
        """Offload compressed context to workspace-accessible storage."""

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
        **kwargs: Any,
    ) -> str:
        """Offload a tool result to workspace-accessible storage."""
