# -*- coding: utf-8 -*-
"""The storage base class."""
from abc import ABC, abstractmethod

from ._models import (
    AgentRecord,
    CredentialRecord,
    SessionRecord,
    WorkspaceRecord,
    SessionData,
)


class StorageBase(ABC):
    """The storage abstract base class."""

    @abstractmethod
    async def upsert_credential(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Create or update a credential in the storage.

        Args:
            user_id (`str`):
                The user id.
            credential_data (`CredentialBase`):
                The credential data.

        Returns:
            `str`:
                The credential id.
        """

    @abstractmethod
    async def list_credentials(self, user_id: str) -> list[CredentialRecord]:
        """List all credentials for a given user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `list[CredentialRecord]`:
                List of all credentials for a given user.
        """

    @abstractmethod
    async def delete_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> bool:
        """Delete a credential.

        Args:
            user_id (`str`):
                The user id.
            credential_id (`str`):
                The credential id.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """

    @abstractmethod
    async def upsert_workspace(
        self,
        user_id: str,
        workspace_data: WorkspaceBase,
    ) -> str:
        """Create a workspace record in the storage.

        Args:
            user_id (`str`):
                The user id.
            workspace_data (`WorkspaceBase`):
                The workspace data.

        Returns:
            `str`:
                The workspace id.
        """

    @abstractmethod
    async def list_workspaces(self, user_id: str) -> list[WorkspaceRecord]:
        """List all workspaces for a given user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `list[WorkspaceRecord]`:
                List of all workspaces for a given user.
        """

    @abstractmethod
    async def delete_workspace(self, user_id: str, workspace_id: str) -> bool:
        """Delete a workspace.

        Args:
            user_id (`str`):
                The user id.
            workspace_id (`str`):
                The workspace id.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """

    @abstractmethod
    async def create_agent(
        self,
        user_id: str,
        agent_data: AgentRecord,
    ) -> str:
        """Create an agent record in the storage.

        Args:
            user_id (`str`):
                The user id.
            agent_data (`AgentRecord`):
                The agent data.

        Returns:
            `str`:
                The agent id.
        """

    @abstractmethod
    async def list_agent(self, user_id: str) -> list[AgentRecord]:
        """List all agents for a given user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `list[AgentRecord]`:
                List of all agents for a given user.
        """

    @abstractmethod
    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete an agent record.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """

    @abstractmethod
    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        workspace_id: str,
        session_data: SessionData,
    ) -> bool:
        """Create a session record in the storage.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.
            workspace_id (`str`):
                The workspace id.
            session_data (`SessionData`):
                The session data.

        Returns:
            `bool`:
                True if updated, False if not found.
        """

    @abstractmethod
    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """List all sessions for a given user and agent entity.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.

        Returns:
            `list[SessionRecord]`:
                List of all sessions for a given user and agent entity.
        """

    @abstractmethod
    async def delete_session(self, user_id: str, session_id: str) -> bool:
        """Delete a session.

        Args:
            user_id (`str`):
                The user id.
            session_id (`str`):
                The session id.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """
