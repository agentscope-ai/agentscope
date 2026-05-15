# -*- coding: utf-8 -*-
"""The storage base class."""
from abc import ABC, abstractmethod
from typing import Any, Self


from ._model import (
    AgentRecord,
    CredentialRecord,
    ScheduleRecord,
    SessionRecord,
    WorkspaceRecord,
    SessionData,
    CredentialBase,
)
from ...workspace import WorkspaceBase


class StorageBase(ABC):
    """The storage abstract base class."""

    async def __aenter__(self) -> Self:
        """Start the storage backend (open connection pool, etc.)."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Shut down the storage backend."""
        await self.aclose()

    async def aclose(self) -> None:
        """Release underlying connection resources. Default is a no-op."""

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
    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord | None:
        """Fetch a single credential record by id.

        Args:
            user_id (`str`): The owner user id.
            credential_id (`str`): The credential id.

        Returns:
            `CredentialRecord | None`: The record, or ``None`` if not found.
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
    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Fetch a single agent record by id.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.

        Returns:
            `AgentRecord | None`: The record, or ``None`` if not found.
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

    @abstractmethod
    async def get_session(
        self,
        user_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Fetch a single session record by id.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.

        Returns:
            `SessionRecord | None`: The record, or ``None`` if not found.
        """

    @abstractmethod
    async def create_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """Persist a cron task record and register it in the user's index.

        Args:
            user_id (`str`): The owner user id.
            record (`ScheduleRecord`): The fully-populated record to store.

        Returns:
            `str`: The id of the stored record.
        """

    @abstractmethod
    async def get_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> ScheduleRecord | None:
        """Fetch a single cron task record by id.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The task id.

        Returns:
            `ScheduleRecord | None`: The record, or ``None`` if not found.
        """

    @abstractmethod
    async def list_schedules(
        self,
        user_id: str,
    ) -> list[ScheduleRecord]:
        """Return all cron task records belonging to the given user.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[ScheduleRecord]`: All cron task records for the user.
        """

    @abstractmethod
    async def delete_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> bool:
        """Delete a cron task record and remove it from the user's index.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The id of the task to delete.

        Returns:
            `bool`: ``True`` if deleted, ``False`` if not found.
        """
