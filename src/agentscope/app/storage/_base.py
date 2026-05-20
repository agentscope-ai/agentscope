# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""The storage base class."""
from abc import ABC, abstractmethod
from typing import Any, Self


from ._model import (
    AgentRecord,
    CredentialRecord,
    ScheduleRecord,
    SessionRecord,
    SessionConfig,
    SessionSource,
)
from ...credential import CredentialBase
from ...message import Msg
from ...state import AgentState


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
    async def upsert_agent(
        self,
        user_id: str,
        agent_record: AgentRecord,
    ) -> str:
        """Create an agent record in the storage.

        Args:
            user_id (`str`):
                The user id.
            agent_record (`AgentRecord`):
                The agent record.

        Returns:
            `str`:
                The agent id.
        """

    @abstractmethod
    async def list_agents(self, user_id: str) -> list[AgentRecord]:
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
        config: SessionConfig,
        state: AgentState | None = None,
        session_id: str | None = None,
        source: SessionSource = SessionSource.USER,
        source_schedule_id: str | None = None,
    ) -> SessionRecord:
        """Create or update a session for a (user, agent) pair.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.
            config (`SessionConfig`): Immutable session configuration
                (model, workspace). Required on create; passed unchanged on
                state-only updates.
            state (`AgentState | None`, optional): Runtime state to persist.
                Defaults to a fresh ``AgentState()`` when ``None``.
            session_id (`str | None`, optional): If provided, update the
                existing session with this id. If ``None``, create a new
                session.
            source (`SessionSource`, optional): The source that created this
                session. Defaults to ``SessionSource.USER``.
            source_schedule_id (`str | None`, optional): The schedule that
                created this session. When set, the session is indexed under
                the schedule for execution history queries.

        Returns:
            `SessionRecord`: The created or updated record.
        """

    @abstractmethod
    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Update only the mutable state of an existing session.

        Convenience method for the hot path (post-chat-turn persistence).
        Raises ``KeyError`` if the session does not exist.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.
            session_id (`str`): The session id.
            state (`AgentState`): The new agent state to persist.
        """

    @abstractmethod
    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """List all sessions for a given user and agent entity.

        Args:
            user_id (`str`): The user id.
            agent_id (`str`): The agent id.

        Returns:
            `list[SessionRecord]`: List of all sessions for the (user, agent).
        """

    @abstractmethod
    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete a session.

        Args:
            user_id (`str`): The user id.
            agent_id (`str`): The agent id.
            session_id (`str`): The session id.

        Returns:
            `bool`: True if deleted, False if not found.
        """

    @abstractmethod
    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Fetch a single session record by id.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.
            session_id (`str`): The session id.

        Returns:
            `SessionRecord | None`: The record, or ``None`` if not found.
        """

    @abstractmethod
    async def list_sessions_by_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> list[SessionRecord]:
        """Return all sessions created by a given schedule.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The schedule id.

        Returns:
            `list[SessionRecord]`: Sessions triggered by this schedule,
            ordered by creation time (newest first).
        """

    @abstractmethod
    async def upsert_schedule(
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

    @abstractmethod
    async def list_all_schedules(self) -> list[ScheduleRecord]:
        """Return every schedule record across all users.

        Used on startup to restore the in-memory scheduler from persisted
        state.  Normal per-user listing should use :meth:`list_schedules`.

        Returns:
            `list[ScheduleRecord]`: All schedule records in the store.
        """

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        msg: Msg,
    ) -> None:
        """Persist a message to the session's message list.

        If the last message in the list has the same ``id`` as *msg*, it is
        replaced (merge/overwrite for the same reply_id across continuation
        calls).  Otherwise, *msg* is appended as a new entry.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            msg (`Msg`): The message to persist.
        """

    @abstractmethod
    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> Msg | None:
        """Fetch a single message by id from the session's message list.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            message_id (`str`): The message id to look up.

        Returns:
            `Msg | None`: The message, or ``None`` if not found.
        """

    @abstractmethod
    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Msg]:
        """Return messages for a session with pagination.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            offset (`int`): Starting index (0-based). Defaults to 0.
            limit (`int`): Maximum number of messages to return.

        Returns:
            `list[Msg]`: Messages in chronological order.
        """
