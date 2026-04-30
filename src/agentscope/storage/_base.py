# -*- coding: utf-8 -*-
"""The storage base class."""
from abc import ABC, abstractmethod
from typing import Any

from ..agent import AgentState


class StorageBase(ABC):
    """The storage abstract base class."""

    @abstractmethod
    async def load_agent_state(
        self,
        session_id: str,
        agent_id: str,
        **kwargs: Any,
    ) -> AgentState:
        """Load the agent state from the storage.

        Args:
            session_id (`str`):
                The session id.
            agent_id (`str`):
                The agent id, in case one session has multiple agents.

        Returns:
            `AgentState`:
                The loaded agent state.
        """

    @abstractmethod
    async def save_agent_state(
        self,
        session_id: str,
        agent_id: str,
        agent_state: AgentState,
        **kwargs: Any,
    ) -> None:
        """Save the agent state to the storage.

        Args:
            session_id (`str`):
                The session id.
            agent_id (`str`):
                The agent id, in case one session has multiple agents.
            agent_state (`AgentState`):
                The agent state.
        """
