# -*- coding: utf-8 -*-
"""The tool-related exceptions in agentscope."""

from ._exception_base import AgentOrientedExceptionBase


class ToolNotFoundError(AgentOrientedExceptionBase):
    """Exception raised when a tool was not found."""


class ToolInterruptedError(AgentOrientedExceptionBase):
    """Exception raised when a tool calling was interrupted by the user."""


class ToolInvalidArgumentsError(AgentOrientedExceptionBase):
    """Exception raised when the arguments passed to a tool are invalid."""
