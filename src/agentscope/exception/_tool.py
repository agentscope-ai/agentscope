# -*- coding: utf-8 -*-
"""The tool-related exceptions in agentscope."""

from ._base import AgentOrientedException


class ToolNotFoundError(AgentOrientedException):
    """Exception raised when a tool was not found."""


class ToolInterruptedError(AgentOrientedException):
    """Exception raised when a tool calling was interrupted by the user."""


class ToolJSONDecodeError(AgentOrientedException):
    """Exception raised when the arguments passed to a tool are invalid."""


class ToolGroupInactiveError(AgentOrientedException):
    """Exception raised when a tool group is inactive."""
