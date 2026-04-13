# -*- coding: utf-8 -*-
"""The base class for session storage."""
from abc import abstractmethod
from typing import Any

from ..agent import AgentState
from ..message import Msg


class StorageBase:
    """Session storage abstraction class.

    In this class, we only list the necessary methods for session management,
    leaving the implementation details to the application developers, e.g.
    if the agent and the conversation session are one-to-many or many-to-many.

    This class provides optional abstractions for frontend rendering, user
    management, and agent management, leaving the implementation details to
    the application developers.
    """

    # ============ History management ============
    # For frontend rendering only, won't affect the actual agent state. If
    # you're maintaining conversation history by yourself, just leave these
    # methods unimplemented.
    async def get_history(
        self,
        session_id: str,
        limit: int,
        user_id: str = "default",
        **kwargs: Any,
    ) -> list[Msg]:
        """Get all the historical messages for a session

        Args:
            session_id (`str`):
                The session id to get the history from.
            limit (`int`):
                Maximum number of messages to return.
            user_id (`str`, defaults to ``"default"``):
                The user id for multi-user storage isolation.
        """
        raise NotImplementedError

    async def upsert_history(
        self,
        session_id: str,
        msgs: list[Msg],
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        """Upsert a message into the session history.

        Args:
            session_id (`str`):
                The session id to upsert the messages into.
            msgs (`list[Msg]`):
                The list of messages to be upserted into the session history.
            user_id (`str`, defaults to ``"default"``):
                The user id for multi-user storage isolation.
        """
        raise NotImplementedError

    # ============ Agent state management ============
    # The agent state in specific session
    @abstractmethod
    async def get_state(
        self,
        session_id: str,
        agent_id: str,
        user_id: str = "default",
        **kwargs: Any,
    ) -> AgentState:
        """Get the current agent state for a given session and agent.

        Args:
            session_id (`str`):
                The session id to get the agent state from.
            agent_id (`str`):
                The agent id to get the state for.
            user_id (`str`, defaults to ``"default"``):
                The user id for multi-user storage isolation.
        """

    @abstractmethod
    async def update_state(
        self,
        session_id: str,
        agent_id: str,
        state: AgentState,
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        """Update the agent state for a given session and agent.

        Args:
            session_id (`str`):
                The session id to update the agent state for.
            agent_id (`str`):
                The agent id to update the state for.
            state (`AgentState`):
                The new agent state to be updated.
            user_id (`str`, defaults to ``"default"``):
                The user id for multi-user storage isolation.
        """
        raise NotImplementedError

    # ============ Session management ============
    # The session management, including creating, deleting and revising a
    # session.
    @abstractmethod
    async def list_sessions(
        self,
        user_id: str = "default",
        *args: Any,
        **kwargs: Any,
    ) -> list[str]:
        """List all available session ids.

        Args:
            user_id (`str`, defaults to ``"default"``):
                The user id for multi-user storage isolation.

        Returns:
            `list[str]`:
                The list of session ids.
        """
        raise NotImplementedError

    @abstractmethod
    async def upsert_session(
        self,
        user_id: str = "default",
        **kwargs: Any,
    ) -> str:
        """Create a new session in the storage and return the session id.

        Args:
            user_id (`str`, defaults to ``"default"``):
                The user id for multi-user storage isolation.

        Returns:
            `str`:
                The created session id.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_session(
        self,
        session_id: str,
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        """Delete a session and all its associated data from the storage.

        Args:
            session_id (`str`):
                The session id to delete.
            user_id (`str`, defaults to ``"default"``):
                The user id for multi-user storage isolation.
        """
        raise NotImplementedError
