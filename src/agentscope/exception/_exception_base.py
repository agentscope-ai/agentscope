# -*- coding: utf-8 -*-
"""The base exception class in agentscope."""


class AgentOrientedExceptionBase(Exception):
    """The base class for all agent-oriented exceptions. These exceptions are
    expect to the captured and exposed to the agent during runtime, so that
    agents can handle the error appropriately during the runtime.
    """

    def __init__(self, message: str):
        """Initialize the exception with a message."""
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        """Return the string representation of the exception."""
        return f"{self.__class__.__name__}: {self.message}"
