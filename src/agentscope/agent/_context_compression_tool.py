# -*- coding: utf-8 -*-
"""The agent-owned tool for explicitly compressing its context."""
from collections.abc import Awaitable, Callable

from ..message import TextBlock, ToolResultState
from ..permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ..tool import ToolBase, ToolChunk


class _CompressContext(ToolBase):
    """Let the agent compress context at a meaningful task boundary."""

    name = "CompressContext"
    description = """Compress older conversation context into a continuation
summary while preserving the recent context needed for upcoming work.

Use this tool only after a runtime-state `<context-compression>` reminder
explicitly recommends it. The reminder indicates that the context is large
enough for compression and that no task was in progress when it was produced.

Do not call this tool while any task is currently `in_progress`, even if an
older compression reminder is still present. Finish or pause the active task
first. Pending tasks do not prevent compression when you are between tasks.

Keep the current context when exact earlier details are still needed verbatim.

Conversation history is replaced only after a summary is generated
successfully."""
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    is_concurrency_safe = False
    is_read_only = False

    def __init__(self, compress: Callable[[], Awaitable[None]]) -> None:
        """Initialize the tool with the owning agent's compression callback."""
        super().__init__()
        self._compress = compress

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow the agent to manage its own internal context."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed.",
        )

    async def call(self) -> ToolChunk:
        """Compress the owning agent's context."""
        try:
            await self._compress()
        except Exception as error:  # pylint: disable=broad-exception-caught
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Context compression failed: {error}",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        return ToolChunk(
            content=[
                TextBlock(
                    text="Context compressed successfully.",
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
