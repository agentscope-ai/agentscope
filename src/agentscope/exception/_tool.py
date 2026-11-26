# -*- coding: utf-8 -*-
"""The tool-related exceptions in agentscope."""

from ._base import AgentOrientedException


class ExternalToolUnavailableError(ValueError):
    """A parked call requires its original result validator."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(
            f"Re-enable {tool_name!r} or use a client configured with the "
            "original tool, then submit the result again.",
        )

    def rejection_details(self) -> dict[str, str]:
        """Expose the recovery action to clients of either transport."""
        return {"unavailable_tool": self.tool_name, "message": str(self)}


class ToolNotFoundError(AgentOrientedException):
    """Exception raised when a tool was not found."""


class ToolInterruptedError(AgentOrientedException):
    """Exception raised when a tool calling was interrupted by the user."""


class ToolJSONDecodeError(AgentOrientedException):
    """Exception raised when tool arguments fail JSON decoding or repair."""


class ToolGroupInactiveError(AgentOrientedException):
    """Exception raised when a tool group is inactive."""
