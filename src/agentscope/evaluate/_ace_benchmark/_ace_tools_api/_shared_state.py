# -*- coding: utf-8 -*-
"""The shared state class for ACEBench simulation tools."""


class SharedState:
    """The sharing state class for ACEBench simulation tools."""

    def __init__(self, shared_state: dict) -> None:
        """Initialize the shared state"""
        self._shared_state = shared_state

    @property
    def wifi(self) -> bool:
        """The WI-FI state"""
        return self._shared_state["wifi"]

    @property
    def logged_in(self) -> bool:
        """The logged in state"""
        return self._shared_state["logged_in"]
