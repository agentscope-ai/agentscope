# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""The storage base class."""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, Self


from ._model import (
    AgentRecord,
    CredentialRecord,
    KnowledgeBaseRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentStatus,
    ScheduleRecord,
    SessionRecord,
    SessionConfig,
    SessionSource,
    TeamRecord,
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
    async def set_session_team_id(
        self,
        user_id: str,
        session_id: str,
        team_id: str | None,
    ) -> None:
        """Set or clear ``team_id`` on an existing session record.

        Bypasses :meth:`upsert_session` because that method does not
        write ``team_id``. Used by team operations (create/dissolve/
        leave) to keep the leader/worker → team relationship consistent.
        Idempotent: a no-op if the session does not exist or already
        holds the given value.

        Args:
            user_id (`str`):
                The owner user id.
            session_id (`str`):
                The session whose ``team_id`` should be updated.
            team_id (`str | None`):
                The new value. ``None`` detaches the session from any
                team.
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
        limit: int = 50,
        before: str | None = None,
        **kwargs: Any,
    ) -> tuple[list[Msg], bool]:
        """Return the most recent messages for a session with
        cursor-based pagination.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            limit (`int`, optional): Maximum number of messages to
                return. Defaults to 50.
            before (`str | None`, optional): A message ID used as the
                cursor. When provided, returns messages created before
                this message. Omit to get the latest page.
            **kwargs: Reserved for backward compatibility. Passing
                ``offset`` will emit a ``DeprecationWarning`` and be
                ignored.

        Returns:
            `tuple[list[Msg], bool]`: A tuple of (messages in
            chronological order, has_more). ``has_more`` is ``True``
            when older messages exist before the returned page.
        """

    # ------------------------------------------------------------------
    # Team persistence
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_team(
        self,
        user_id: str,
        record: TeamRecord,
    ) -> TeamRecord:
        """Create or update a team record.

        Args:
            user_id (`str`): The owner user id.
            record (`TeamRecord`): The team record to persist. The record's
                ``id`` is used as the primary key; if a record with the same
                id already exists it is overwritten.

        Returns:
            `TeamRecord`: The stored record (with ``updated_at`` refreshed).
        """

    @abstractmethod
    async def get_team(
        self,
        user_id: str,
        team_id: str,
    ) -> TeamRecord | None:
        """Fetch a single team record by id.

        Args:
            user_id (`str`): The owner user id.
            team_id (`str`): The team id.

        Returns:
            `TeamRecord | None`: The record, or ``None`` if not found.
        """

    @abstractmethod
    async def list_teams(self, user_id: str) -> list[TeamRecord]:
        """List all teams owned by a given user.

        Args:
            user_id (`str`): The user id.

        Returns:
            `list[TeamRecord]`: All team records belonging to the user.
        """

    @abstractmethod
    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Delete a team record and cascade-clean its members by role.

        The cascade is role-aware — the two team-membership modes
        (created vs invited, see :class:`TeamMember`) must be handled
        differently:

        1. For each :class:`TeamMember` in the team's roster (resolved
           via the ``ensure_team_members`` helper so legacy
           ``member_ids``-only records are migrated on first read):

           - ``role == "created"`` — call :meth:`delete_agent`
             (which cascades that worker's session). The agent record
             is fully removed because it was spawned solely for this
             team.
           - ``role == "invited"`` — call :meth:`delete_session` for
             the borrowed team-scoped session only. The invited
             agent's :class:`AgentRecord` and any other sessions it
             owns survive the team's dissolution.
        2. Clear ``team_id`` on the leader session referenced by
           :attr:`TeamRecord.session_id` (``ON DELETE SET NULL`` for
           the leader's back-reference). Idempotent if the session has
           already been deleted.
        3. Delete the :class:`TeamRecord` key and the per-user team
           index entry.

        Args:
            user_id (`str`):
                The owner user id.
            team_id (`str`):
                The id of the team to delete.

        Returns:
            `bool`:
                ``True`` if the team record existed and was deleted,
                ``False`` if not found.
        """

    # ------------------------------------------------------------------
    # Knowledge base persistence
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_knowledge_base(
        self,
        user_id: str,
        record: KnowledgeBaseRecord,
    ) -> KnowledgeBaseRecord:
        """Create or update a knowledge base record.

        The caller is responsible for constructing the full
        :class:`KnowledgeBaseRecord` (including ``id`` and
        ``collection_name``).  If a record with the same id already
        exists it is overwritten and ``updated_at`` refreshed.

        Args:
            user_id (`str`):
                The owner user id. Must match ``record.user_id``.
            record (`KnowledgeBaseRecord`):
                The fully-populated record to persist.

        Returns:
            `KnowledgeBaseRecord`:
                The stored record (with ``updated_at`` refreshed).
        """

    @abstractmethod
    async def get_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> KnowledgeBaseRecord | None:
        """Fetch a single knowledge base record by id.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The knowledge base id.

        Returns:
            `KnowledgeBaseRecord | None`:
                The record, or ``None`` if not found or not owned by
                the given user.
        """

    @abstractmethod
    async def list_knowledge_bases(
        self,
        user_id: str,
    ) -> list[KnowledgeBaseRecord]:
        """List all knowledge base records owned by the given user.

        Args:
            user_id (`str`):
                The owner user id.

        Returns:
            `list[KnowledgeBaseRecord]`:
                All knowledge base records belonging to the user.
        """

    @abstractmethod
    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> bool:
        """Delete a knowledge base record and remove it from the user index.

        Note: this only removes the metadata record; deletion of the
        underlying vector store collection is the caller's
        responsibility (typically the knowledge base manager).

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The id of the record to delete.

        Returns:
            `bool`:
                ``True`` if the record existed and was deleted,
                ``False`` if not found.
        """

    # ------------------------------------------------------------------
    # Knowledge document persistence
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_knowledge_document(
        self,
        user_id: str,
        record: KnowledgeDocumentRecord,
    ) -> KnowledgeDocumentRecord:
        """Create or update a knowledge document record.

        Used by the upload endpoint to register a freshly arrived
        document (``status='pending'``) and by other code paths that
        need to overwrite the full record.  Phase transitions during
        indexing should go through :meth:`update_knowledge_document_status`
        instead, which is cheaper and atomic w.r.t. the lease fields.

        Args:
            user_id (`str`):
                The owner user id.  Must match ``record.user_id``.
            record (`KnowledgeDocumentRecord`):
                The fully-populated record to persist.

        Returns:
            `KnowledgeDocumentRecord`:
                The stored record (with ``updated_at`` refreshed).
        """

    @abstractmethod
    async def get_knowledge_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeDocumentRecord | None:
        """Fetch a single knowledge document record by id.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document id.

        Returns:
            `KnowledgeDocumentRecord | None`:
                The record, or ``None`` if not found.
        """

    @abstractmethod
    async def list_knowledge_documents(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> list[KnowledgeDocumentRecord]:
        """List all documents in a knowledge base.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.

        Returns:
            `list[KnowledgeDocumentRecord]`:
                All document records belonging to the knowledge base,
                in arbitrary order.
        """

    @abstractmethod
    async def delete_knowledge_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> bool:
        """Delete a knowledge document record.

        Only removes the metadata record; cleanup of the underlying
        blob and vector store records is the caller's responsibility.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The id of the record to delete.

        Returns:
            `bool`:
                ``True`` if the record existed and was deleted,
                ``False`` if not found.
        """

    @abstractmethod
    async def update_knowledge_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        status: KnowledgeDocumentStatus,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Update only the status-related fields of a document record.

        Used by the indexing worker as it walks the lifecycle
        transitions (``parsing`` → ``chunking`` → ``indexing`` →
        ``ready`` / ``error``).  Cheaper than a full upsert and avoids
        races with concurrent lease writes by touching only the status
        / error / chunk_count fields.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document being updated.
            status (`KnowledgeDocumentStatus`):
                The new lifecycle state.
            error (`str | None`, optional):
                Failure reason, set when ``status == 'error'``.
                Ignored otherwise.  ``None`` leaves the existing
                value unchanged.
            chunk_count (`int | None`, optional):
                The final chunk count, set when ``status == 'ready'``.
                ``None`` leaves the existing value unchanged.
        """

    @abstractmethod
    async def acquire_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
        lease_ttl: timedelta,
        now: datetime | None = None,
    ) -> bool:
        """Compare-and-swap acquisition of the document processing lease.

        Succeeds only if no other worker currently holds a live lease —
        i.e. ``processing_node`` is unset or ``lease_expires_at`` is in
        the past relative to ``now``.  On success the record's
        ``processing_node`` and ``data.lease_expires_at`` fields are
        updated.  The CAS is what makes the sweeper safe to run on
        multiple nodes at once: even if two sweepers redispatch the
        same document, only one worker can acquire its lease.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document whose lease is being acquired.
            processing_node (`str`):
                Stable identifier of the calling worker (e.g.
                ``hostname:pid:uuid``).  Persisted as
                ``processing_node`` on success.
            lease_ttl (`timedelta`):
                How long the lease should live from ``now``.
            now (`datetime | None`, optional):
                Reference time for the comparison.  Defaults to
                ``datetime.now()``.  Injectable for testing.

        Returns:
            `bool`:
                ``True`` when the lease was acquired by this caller,
                ``False`` when another worker already holds it.
        """

    @abstractmethod
    async def renew_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
        lease_ttl: timedelta,
        now: datetime | None = None,
    ) -> bool:
        """Extend an existing lease this worker already holds.

        Required for long-running parses that exceed ``lease_ttl`` —
        the worker calls this periodically so the sweeper does not
        mistake it for a crash.  Updates ``lease_expires_at`` to
        ``now + lease_ttl`` only when ``processing_node`` matches the
        caller; otherwise returns ``False`` so the worker can abandon
        cleanly (its lease was stolen).

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document being renewed.
            processing_node (`str`):
                The caller's processing node id; must match the
                record's current ``processing_node`` for renewal to
                succeed.
            lease_ttl (`timedelta`):
                The new lease duration relative to ``now``.
            now (`datetime | None`, optional):
                Reference time.  Defaults to ``datetime.now()``.

        Returns:
            `bool`:
                ``True`` when the renewal succeeded, ``False`` when
                the lease no longer belongs to the caller.
        """

    @abstractmethod
    async def release_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
    ) -> None:
        """Release the processing lease this worker holds.

        Clears ``processing_node`` and ``data.lease_expires_at`` only
        if the current holder matches ``processing_node`` — a stolen
        lease (e.g. after sweep) is left untouched.  Idempotent: a
        no-op when the document is missing or already free.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document whose lease is being released.
            processing_node (`str`):
                The caller's processing node id; must match the
                record's current holder.
        """

    @abstractmethod
    async def list_knowledge_documents_with_expired_lease(
        self,
        now: datetime | None = None,
    ) -> list[KnowledgeDocumentRecord]:
        """Return non-terminal documents whose lease has expired.

        Scans every user / knowledge base — used by the sweeper, not
        by user-facing endpoints.  A document is "expired" when
        ``data.status`` is not terminal (``ready`` / ``error``),
        ``processing_node`` is set, and ``data.lease_expires_at`` is
        in the past.  Implementations are free to skip records that
        match no work and return an unspecified order.

        Args:
            now (`datetime | None`, optional):
                Reference time.  Defaults to ``datetime.now()``.

        Returns:
            `list[KnowledgeDocumentRecord]`:
                Documents to redispatch.
        """

    @abstractmethod
    async def list_knowledge_documents_pending_since(
        self,
        threshold: datetime,
    ) -> list[KnowledgeDocumentRecord]:
        """Return documents stuck in ``pending`` older than ``threshold``.

        Catches the corner case where the upload endpoint persisted a
        record but the dispatcher (or the process holding it) died
        before any worker picked the document up — a crashed lease
        sweep would miss it because no lease was ever written.

        Args:
            threshold (`datetime`):
                Cut-off creation time; only ``pending`` records
                ``created_at < threshold`` are returned.

        Returns:
            `list[KnowledgeDocumentRecord]`:
                Orphan ``pending`` documents to redispatch.
        """
