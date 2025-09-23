# -*- coding: utf-8 -*-
"""The session base class in agentscope."""
from abc import abstractmethod

from ..module import StateModule


class SessionBase:
    """The base class for session in agentscope."""

    @abstractmethod
    async def save_session_state(
        self,
        session_id: str,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Save the session state

        Args:
            session_id (`str`):
                The session id.
            **state_modules_mapping (`dict[str, StateModule]`):
                A dictionary mapping of state module names to their instances.
        """

    @abstractmethod
    async def load_session_state(
        self,
        session_id: str,
        allow_not_exist: bool = True,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the session state"""
