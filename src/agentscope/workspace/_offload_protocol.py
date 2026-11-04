# -*- coding: utf-8 -*-
"""The offload protocol."""
from typing import Protocol

from ..message import DataBlock, Msg, ToolResultBlock


class Offloader(Protocol):
    """The offloader protocol."""

    async def offload_data_block(self, block: DataBlock) -> DataBlock:
        """Persist a base64 data block to workspace storage.

        Args:
            block (`DataBlock`):
                A data block. Blocks already backed by a URL source are
                returned unchanged.

        Returns:
            `DataBlock`:
                A data block whose source is a portable ``workspace://``
                URL pointing at the persisted file inside the workspace.
        """

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Offload compressed context to workspace-accessible storage.

        Args:
            session_id (`str`):
                The session id.
            msgs (`list[Msg]`):
                The messages to offload.

        Returns:
            `str`:
                The offloaded context reference.
        """

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Offload a tool result to workspace-accessible storage.

        Args:
            session_id (`str`):
                The session id.
            tool_result (`ToolResultBlock`):
                The tool result.

        Returns:
            `str`:
                The offloaded context reference.
        """
