# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""An in-memory storage implementation backed by Python dicts and lists.

Suitable for development, testing, and single-process deployments.  All data
resides in process memory — restarting the process loses all persisted state.
"""
from collections import defaultdict
from datetime import datetime
from typing import Any

from ._base import StorageBase
from ._model import (
    AgentRecord,
    CredentialRecord,
    ScheduleRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
    TeamRecord,
)
from ._utils import _dump_with_secrets
from ...credential import CredentialBase
from ...message import Msg
from ...state import AgentState


class MemStorage(StorageBase):
    """An in-memory storage implementation.

    All data is stored in nested :class:`dict` and :class:`list` structures
    keyed by ``user_id`` → entity type → entity id.  Indexes (sets of ids)
    are stored alongside the records.

    Record objects are isolated via JSON round-trip
    (``model_dump_json`` / ``model_validate_json``) on both read and write,
    matching the semantics of :class:`RedisStorage`.  Messages are stored as
    live :class:`Msg` objects for efficiency — callers must not mutate
    returned message objects.

    Usage::

        async with MemStorage() as storage:
            await storage.upsert_credential(user_id, cred_data)
            records = await storage.list_credentials(user_id)

    .. note::
        This backend is **not** suitable for multi-process deployments
        because the data lives in a single Python process.  Use
        :class:`RedisStorage` for production service setups.
    """

    def __init__(self) -> None:
        """Initialise the in-memory store with empty data structures."""
        # ── Record storage ──────────────────────────────────────────
        # Structure: _records[user_id][entity_kind][entity_id] = json_str
        # Backed by nested defaultdicts for auto-vivification.
        self._records: defaultdict[
            str, defaultdict[str, dict[str, str]],
        ] = defaultdict(lambda: defaultdict(dict))

        # ── Index storage ───────────────────────────────────────────
        # Structure: _indexes[user_id][index_name] = set of ids
        # Backed by nested defaultdicts for auto-vivification.
        self._indexes: defaultdict[
            str, defaultdict[str, set[str]],
        ] = defaultdict(lambda: defaultdict(set))

        # ── Message storage ─────────────────────────────────────────
        # Structure: _messages[user_id][session_key] = list[Msg]
        # Backed by nested defaultdicts for auto-vivification.
        self._messages: defaultdict[
            str, defaultdict[str, list[Msg]],
        ] = defaultdict(lambda: defaultdict(list))

    # ==================================================================
    # Infrastructure helpers
    # ==================================================================

    async def aclose(self) -> None:
        """Release resources — no-op for the in-memory backend."""
        self._records.clear()
        self._indexes.clear()
        self._messages.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entity_kind(entity: str) -> str:
        """Map entity name to the key used inside ``_records``."""
        _MAP = {
            "credential": "credentials",
            "agent": "agents",
            "session": "sessions",
            "schedule": "schedules",
            "team": "teams",
        }
        return _MAP[entity]

    @staticmethod
    def _index_name(entity: str) -> str:
        """Map entity name to the key used inside ``_indexes``."""
        _MAP = {
            "credential": "credential_ids",
            "agent": "agent_ids",
            "session": "session_ids",
            "schedule": "schedule_ids",
            "team": "team_ids",
        }
        return _MAP[entity]

    def _session_index_key(self, _user_id: str, agent_id: str) -> str:
        """Composite key for per-(user, agent) session indexes."""
        return f"session_ids:{agent_id}"

    @staticmethod
    def _schedule_session_index_key(user_id: str) -> str:
        """Composite key for per-(user, schedule) session indexes.

        ``user_id`` is accepted for signature parity with the Redis
        backend but is not embedded in the key — MemStorage only needs
        the composite ``schedule_id`` portion.
        """
        _ = user_id
        return "schedule_session_ids"

    @staticmethod
    def _message_key(user_id: str, session_id: str) -> str:
        """Normalise the composite key used for session message lists.

        ``user_id`` is accepted for signature parity with the Redis
        backend but is not embedded in the key — MemStorage only needs
        the ``session_id`` portion.
        """
        _ = user_id
        return f"messages:{session_id}"

    async def _generate_credential_name(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Auto-generate a display name for a credential based on its type.

        Produces names like "OpenAI", "OpenAI (2)", "OpenAI (3)", etc.
        """
        cred_type = getattr(credential_data, "type", "")
        base_name = (
            cred_type.removesuffix("_credential").replace("_", " ").title()
        )
        if not base_name:
            base_name = "Credential"

        existing = await self.list_credentials(user_id)
        same_type_names = [
            c.data.get("name", "")
            for c in existing
            if c.data.get("type") == cred_type and c.id != credential_data.id
        ]

        if base_name not in same_type_names:
            return base_name

        idx = 2
        while f"{base_name} ({idx})" in same_type_names:
            idx += 1
        return f"{base_name} ({idx})"

    # ==================================================================
    # Credential CRUD
    # ==================================================================

    async def upsert_credential(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Create or update a credential record for the given user.

        If ``credential_data.id`` is set and the record already exists, the
        existing record's ``data`` field is updated in place (preserving
        ``created_at``). If the id is set but no record exists, a new record
        is created with that id.  If ``credential_data.id`` is ``None``, a
        new record with a generated id is always created.

        Args:
            user_id (`str`):
                The owner user id.
            credential_data (`CredentialBase`):
                Input data containing an optional ``id`` and the credential
                data.

        Returns:
            `str`:
                The id of the created or updated credential record.
        """
        if not credential_data.name:
            credential_data.name = await self._generate_credential_name(
                user_id,
                credential_data,
            )

        data_dump = _dump_with_secrets(credential_data)
        kind = self._entity_kind("credential")
        index = self._index_name("credential")

        if credential_data.id and credential_data.id in self._records[
            user_id
        ][kind]:
            record = CredentialRecord.model_validate_json(
                self._records[user_id][kind][credential_data.id],
            )
            record.data = data_dump
            record.updated_at = datetime.now()
        elif credential_data.id:
            record = CredentialRecord(
                id=credential_data.id,
                user_id=user_id,
                data=data_dump,
            )
        else:
            record = CredentialRecord(
                user_id=user_id,
                data=data_dump,
            )

        self._records[user_id][kind][record.id] = record.model_dump_json()
        self._indexes[user_id][index].add(record.id)
        return record.id

    async def list_credentials(self, user_id: str) -> list[CredentialRecord]:
        """Return all credential records belonging to the given user.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[CredentialRecord]`: All credential records for the user.
        """
        kind = self._entity_kind("credential")
        index = self._index_name("credential")
        records: list[CredentialRecord] = []
        for cred_id in self._indexes[user_id][index]:
            raw = self._records[user_id][kind].get(cred_id)
            if raw:
                records.append(CredentialRecord.model_validate_json(raw))
        return records

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
        kind = self._entity_kind("credential")
        raw = self._records[user_id][kind].get(credential_id)
        if not raw:
            return None
        return CredentialRecord.model_validate_json(raw)

    async def delete_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> bool:
        """Delete a credential record and remove it from the user's index.

        Args:
            user_id (`str`): The owner user id.
            credential_id (`str`): The id of the credential to delete.

        Returns:
            `bool`: ``True`` if the record existed and was deleted,
            ``False`` if it did not exist.
        """
        kind = self._entity_kind("credential")
        index = self._index_name("credential")
        record_kind = self._records[user_id][kind]
        if credential_id not in record_kind:
            return False
        del record_kind[credential_id]
        self._indexes[user_id][index].discard(credential_id)
        return True

    # ==================================================================
    # Agent CRUD
    # ==================================================================

    async def upsert_agent(
        self,
        user_id: str,
        agent_record: AgentRecord,
    ) -> str:
        """Persist an agent record and register it in the user's agent index.

        The caller is responsible for constructing the full ``AgentRecord``
        (including its ``id``). If a record with the same id already exists
        it will be overwritten.

        Args:
            user_id (`str`):
                The owner user id.
            agent_record (`AgentRecord`):
                The fully-populated agent record to store.

        Returns:
            `str`:
                The id of the stored agent record.
        """
        kind = self._entity_kind("agent")
        index = self._index_name("agent")
        self._records[user_id][kind][agent_record.id] = (
            agent_record.model_dump_json()
        )
        self._indexes[user_id][index].add(agent_record.id)
        return agent_record.id

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """Return user-facing agent records (``source='user'``).

        Filters out team-spawned workers (``source='team'``) — those are
        scoped to a team and only addressable via team detail / direct id
        lookup.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[AgentRecord]`:
                All ``source='user'`` agent records for the user.
        """
        kind = self._entity_kind("agent")
        index = self._index_name("agent")
        records: list[AgentRecord] = []
        for agent_id in self._indexes[user_id][index]:
            raw = self._records[user_id][kind].get(agent_id)
            if raw:
                record = AgentRecord.model_validate_json(raw)
                if record.source == "user":
                    records.append(record)
        return records

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
        kind = self._entity_kind("agent")
        raw = self._records[user_id][kind].get(agent_id)
        if not raw:
            return None
        return AgentRecord.model_validate_json(raw)

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete an agent record and cascade-delete its sessions,
        schedules, and any team back-references.

        Cascade order:

        1. **Sessions** — every session belonging to this agent is
           deleted via :meth:`delete_session` (which itself cascades
           message log, schedule-session index, and — if a session leads
           a team — the team).
        2. **Schedules** — every schedule whose ``data.agent_id`` matches
           is deleted via :meth:`delete_schedule`.
        3. **Team back-references (defensive)** — if the agent is a team
           worker (``source='team'``) but the caller chose to delete it
           directly instead of going through :meth:`delete_team`, scan
           the user's teams and remove the agent id from every
           :attr:`TeamData.member_ids` list it appears in.
        4. **Agent record + index** — finally delete the agent key and
           remove from the per-user agent index.

        Args:
            user_id (`str`):
                The owner user id.
            agent_id (`str`):
                The id of the agent to delete.

        Returns:
            `bool`:
                ``True`` if the agent record existed and was deleted,
                ``False`` if it did not exist.
        """
        kind = self._entity_kind("agent")
        index = self._index_name("agent")
        if agent_id not in self._records[user_id][kind]:
            return False

        # Cascade: sessions
        sessions = await self.list_sessions(user_id, agent_id)
        for session in sessions:
            _ = await self.delete_session(user_id, agent_id, session.id)

        # Cascade: schedules owned by this agent
        schedules = await self.list_schedules(user_id)
        for schedule in schedules:
            if schedule.agent_id == agent_id:
                _ = await self.delete_schedule(user_id, schedule.id)

        # Defensive: scrub agent_id from any team's member_ids list.
        teams = await self.list_teams(user_id)
        for team in teams:
            if agent_id in team.data.member_ids:
                team.data.member_ids = [
                    mid
                    for mid in team.data.member_ids
                    if mid != agent_id
                ]
                _ = await self.upsert_team(user_id, team)

        del self._records[user_id][kind][agent_id]
        self._indexes[user_id][index].discard(agent_id)
        return True

    # ==================================================================
    # Session CRUD
    # ==================================================================

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

        When *session_id* is provided the existing session is updated.
        When *session_id* is ``None`` a new session is always created.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.
            config (`SessionConfig`): Immutable session configuration.
            state (`AgentState | None`, optional): Runtime state to persist.
            session_id (`str | None`, optional): If provided, update the
                existing session with this id.
            source (`SessionSource`, optional): Creation source.
            source_schedule_id (`str | None`, optional): Schedule that
                created this session.

        Returns:
            `SessionRecord`: The created or updated record.
        """
        kind = self._entity_kind("session")
        index = self._index_name("session")
        session_idx_key = self._session_index_key(user_id, agent_id)

        if session_id and session_id in self._records[user_id][kind]:
            raw = self._records[user_id][kind][session_id]
            record = SessionRecord.model_validate_json(raw)
            record.config = config
            if state is not None:
                record.state = state
            record.updated_at = datetime.now()
            self._records[user_id][kind][session_id] = record.model_dump_json()
            return record

        new_id_kwargs: dict[str, Any] = (
            {"id": session_id} if session_id else {}
        )
        record = SessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            config=config,
            source=source,
            source_schedule_id=source_schedule_id,
            state=state if state is not None else AgentState(),
            **new_id_kwargs,
        )
        self._records[user_id][kind][record.id] = record.model_dump_json()
        self._indexes[user_id][index].add(record.id)
        self._indexes[user_id][session_idx_key].add(record.id)

        if source_schedule_id:
            sched_key = self._schedule_session_index_key(user_id)
            self._indexes[user_id][f"{sched_key}:{source_schedule_id}"].add(
                record.id,
            )

        return record

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Update only the mutable state of an existing session.

        Raises:
            KeyError: If the session does not exist.
        """
        kind = self._entity_kind("session")
        raw = self._records[user_id][kind].get(session_id)
        if raw is None:
            raise KeyError(f"Session {session_id!r} not found.")
        record = SessionRecord.model_validate_json(raw)
        record.state = state
        record.updated_at = datetime.now()
        self._records[user_id][kind][session_id] = record.model_dump_json()

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Return all session records for a given (user, agent) pair.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id whose sessions to list.

        Returns:
            `list[SessionRecord]`: All session records for the (user, agent)
            pair, ordered by creation time (newest first).
        """
        kind = self._entity_kind("session")
        session_idx_key = self._session_index_key(user_id, agent_id)
        records: list[SessionRecord] = []
        for session_id in self._indexes[user_id][session_idx_key]:
            raw = self._records[user_id][kind].get(session_id)
            if raw:
                records.append(SessionRecord.model_validate_json(raw))
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

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
        kind = self._entity_kind("session")
        raw = self._records[user_id][kind].get(session_id)
        if not raw:
            return None
        return SessionRecord.model_validate_json(raw)

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete a session record and cascade clean-up.

        Cascades:

        - Existing: per-session message log, schedule-session index entry.
        - If this session is the leader of a team, call
          :meth:`delete_team` first.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The id of the agent that owns the session.
            session_id (`str`): The id of the session to delete.

        Returns:
            `bool`: ``True`` if the session existed and was deleted,
            ``False`` if no record was found.
        """
        kind = self._entity_kind("session")
        index = self._index_name("session")
        session_idx_key = self._session_index_key(user_id, agent_id)
        msg_key = self._message_key(user_id, session_id)

        raw = self._records[user_id][kind].get(session_id)
        if raw is None:
            return False

        record = SessionRecord.model_validate_json(raw)

        # Cascade: if this session leads a team, dissolve it first.
        if record.team_id:
            team = await self.get_team(user_id, record.team_id)
            if team is not None and team.session_id == session_id:
                _ = await self.delete_team(user_id, record.team_id)

        del self._records[user_id][kind][session_id]
        self._indexes[user_id][index].discard(session_id)
        self._indexes[user_id][session_idx_key].discard(session_id)
        _ = self._messages[user_id].pop(msg_key, None)

        if record.source_schedule_id:
            sched_key_base = self._schedule_session_index_key(user_id)
            sched_key = f"{sched_key_base}:{record.source_schedule_id}"
            self._indexes[user_id][sched_key].discard(session_id)

        return True

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
        kind = self._entity_kind("session")
        sched_key_base = self._schedule_session_index_key(user_id)
        sched_key = f"{sched_key_base}:{schedule_id}"
        records: list[SessionRecord] = []
        for session_id in self._indexes[user_id][sched_key]:
            raw = self._records[user_id][kind].get(session_id)
            if raw:
                records.append(SessionRecord.model_validate_json(raw))
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    # ==================================================================
    # Schedule CRUD
    # ==================================================================

    async def upsert_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """Persist a cron task record and register it in the user and global
        indexes.

        Args:
            user_id (`str`): The owner user id.
            record (`ScheduleRecord`): The fully-populated record to store.

        Returns:
            `str`: The id of the stored record.
        """
        kind = self._entity_kind("schedule")
        index = self._index_name("schedule")
        global_index = "schedule_global_ids"
        self._records[user_id][kind][record.id] = record.model_dump_json()
        self._indexes[user_id][index].add(record.id)
        self._indexes["__global__"][global_index].add(
            f"{user_id}:{record.id}",
        )
        return record.id

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
        kind = self._entity_kind("schedule")
        raw = self._records[user_id][kind].get(schedule_id)
        if not raw:
            return None
        return ScheduleRecord.model_validate_json(raw)

    async def list_schedules(self, user_id: str) -> list[ScheduleRecord]:
        """Return all cron task records belonging to the given user.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[ScheduleRecord]`: All cron task records for the user.
        """
        kind = self._entity_kind("schedule")
        index = self._index_name("schedule")
        records: list[ScheduleRecord] = []
        for schedule_id in self._indexes[user_id][index]:
            raw = self._records[user_id][kind].get(schedule_id)
            if raw:
                records.append(ScheduleRecord.model_validate_json(raw))
        return records

    async def delete_schedule(self, user_id: str, schedule_id: str) -> bool:
        """Delete a cron task record, cascade-delete its execution sessions,
        and remove it from the user and global indexes.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The id of the task to delete.

        Returns:
            `bool`: ``True`` if deleted, ``False`` if not found.
        """
        kind = self._entity_kind("schedule")
        index = self._index_name("schedule")
        global_index = "schedule_global_ids"

        raw = self._records[user_id][kind].get(schedule_id)
        if raw is None:
            return False

        record = ScheduleRecord.model_validate_json(raw)

        # Cascade: delete all sessions created by this schedule
        sessions = await self.list_sessions_by_schedule(user_id, schedule_id)
        for session in sessions:
            _ = await self.delete_session(
                user_id,
                record.agent_id,
                session.id,
            )

        # Clean up the schedule-session index key
        sched_key_base = self._schedule_session_index_key(user_id)
        sched_key = f"{sched_key_base}:{schedule_id}"
        _ = self._indexes[user_id].pop(sched_key, None)

        del self._records[user_id][kind][schedule_id]
        self._indexes[user_id][index].discard(schedule_id)
        self._indexes["__global__"][global_index].discard(
            f"{user_id}:{schedule_id}",
        )
        return True

    async def list_all_schedules(self) -> list[ScheduleRecord]:
        """Return every schedule record across all users.

        Used on startup to restore the in-memory scheduler from persisted
        state.

        Returns:
            `list[ScheduleRecord]`: All schedule records in the store.
        """
        global_index = "schedule_global_ids"
        kind = self._entity_kind("schedule")
        records: list[ScheduleRecord] = []
        for entry in self._indexes["__global__"][global_index]:
            user_id, schedule_id = entry.split(":", 1)
            raw = self._records[user_id][kind].get(schedule_id)
            if raw:
                records.append(ScheduleRecord.model_validate_json(raw))
        return records

    # ==================================================================
    # Message persistence
    # ==================================================================

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        msg: Msg,
    ) -> None:
        """Persist a message to the session's message list.

        If the last message in the list has the same ``id`` as *msg*, it is
        replaced (merge/overwrite for the same reply_id across continuation
        calls). Otherwise, *msg* is appended as a new entry.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            msg (`Msg`): The message to persist.
        """
        key = self._message_key(user_id, session_id)
        msgs = self._messages[user_id][key]
        msg_copy = msg.model_copy(deep=True)
        if msgs and msgs[-1].id == msg_copy.id:
            msgs[-1] = msg_copy
        else:
            msgs.append(msg_copy)

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
        key = self._message_key(user_id, session_id)
        for msg in reversed(self._messages[user_id][key]):
            if msg.id == message_id:
                return msg
        return None

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
        key = self._message_key(user_id, session_id)
        msgs = self._messages[user_id][key]
        return msgs[offset : offset + limit]

    # ==================================================================
    # Team persistence
    # ==================================================================

    async def upsert_team(
        self,
        user_id: str,
        record: TeamRecord,
    ) -> TeamRecord:
        """Persist a team record and register it in the user's team index.

        Args:
            user_id (`str`):
                The owner user id. Used to scope both the record key and
                the per-user team index.
            record (`TeamRecord`):
                The team record to persist. Its ``id`` is used as the
                primary key; an existing record with the same id is
                overwritten. ``updated_at`` is refreshed to
                ``datetime.now()`` before writing.

        Returns:
            `TeamRecord`:
                The stored record (with refreshed ``updated_at``).
        """
        record.updated_at = datetime.now()
        kind = self._entity_kind("team")
        index = self._index_name("team")
        self._records[user_id][kind][record.id] = record.model_dump_json()
        self._indexes[user_id][index].add(record.id)
        return record

    async def get_team(
        self,
        user_id: str,
        team_id: str,
    ) -> TeamRecord | None:
        """Fetch a single team record by id.

        Args:
            user_id (`str`):
                The owner user id.
            team_id (`str`):
                The team id to look up.

        Returns:
            `TeamRecord | None`:
                The record, or ``None`` if no record exists.
        """
        kind = self._entity_kind("team")
        raw = self._records[user_id][kind].get(team_id)
        if not raw:
            return None
        return TeamRecord.model_validate_json(raw)

    async def list_teams(self, user_id: str) -> list[TeamRecord]:
        """Return all team records belonging to the given user.

        Args:
            user_id (`str`):
                The owner user id whose teams to list.

        Returns:
            `list[TeamRecord]`:
                All team records for the user, in arbitrary order.
        """
        kind = self._entity_kind("team")
        index = self._index_name("team")
        records: list[TeamRecord] = []
        for team_id in self._indexes[user_id][index]:
            raw = self._records[user_id][kind].get(team_id)
            if raw:
                records.append(TeamRecord.model_validate_json(raw))
        return records

    async def set_session_team_id(
        self,
        user_id: str,
        session_id: str,
        team_id: str | None,
    ) -> None:
        """Set or clear ``team_id`` on an existing session record.

        Bypasses :meth:`upsert_session` because that method does not
        allow writing ``team_id``. Idempotent: a no-op if the session
        does not exist or already holds the given value.

        Args:
            user_id (`str`):
                The owner user id.
            session_id (`str`):
                The session whose ``team_id`` should be updated.
            team_id (`str | None`):
                The new value. ``None`` detaches the session from any
                team.
        """
        kind = self._entity_kind("session")
        raw = self._records[user_id][kind].get(session_id)
        if raw is None:
            return
        record = SessionRecord.model_validate_json(raw)
        if record.team_id == team_id:
            return
        record.team_id = team_id
        record.updated_at = datetime.now()
        self._records[user_id][kind][session_id] = record.model_dump_json()

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Delete a team record and cascade-delete all of its workers.

        Cascade order:

        1. For each ``member_id`` in :attr:`TeamData.member_ids`, call
           :meth:`delete_agent` (which cascades that worker's session).
        2. Clear ``team_id`` on the leader session.
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
                ``False`` if no record was found.
        """
        team = await self.get_team(user_id, team_id)
        if team is None:
            kind = self._entity_kind("team")
            index = self._index_name("team")
            # Defensive cleanup: remove orphan index entries
            deleted_record = team_id in self._records[user_id][kind]
            if deleted_record:
                del self._records[user_id][kind][team_id]
            self._indexes[user_id][index].discard(team_id)
            return deleted_record

        # Cascade: delete each worker agent (which cascades its session)
        for member_id in team.data.member_ids:
            _ = await self.delete_agent(user_id, member_id)

        # Clear team_id on the leader session (idempotent)
        await self.set_session_team_id(user_id, team.session_id, None)

        kind = self._entity_kind("team")
        index = self._index_name("team")
        existed = team_id in self._records[user_id][kind]
        _ = self._records[user_id][kind].pop(team_id, None)
        self._indexes[user_id][index].discard(team_id)
        return existed
