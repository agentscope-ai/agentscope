# -*- coding: utf-8 -*-
"""The session base class in agentscope."""
from abc import abstractmethod
from typing import Any

from ..module import StateModule


class SessionBase:
    """The base class for session in agentscope."""

    session_id: str
    """The session id"""

    def __init__(self, session_id: str) -> None:
        """Initialize the session base class"""

        self.session_id = session_id

    @abstractmethod
    async def save_session_state(
        self,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Save the session state"""

    @abstractmethod
    async def load_session_state(self, *args: Any, **kwargs: Any) -> None:
        """Load the session state"""
