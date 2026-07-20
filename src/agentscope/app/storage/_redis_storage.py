# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""The Redis storage implementation."""

import warnings
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING, Self

from pydantic import BaseModel

from ._base import StorageBase
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
from ._utils import _dump_with_secrets
from ...credential import CredentialBase
from ...message import Msg
from ...state import AgentState

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis
else:
    ConnectionPool = Any
    Redis = Any


def _watch_error() -> type[BaseException]:
    """Return the ``WatchError`` class from ``redis.exceptions``.

    Lazy-imported so ``redis`` stays an optional dependency at module
    load time — same trick the storage class uses in ``__aenter__``.
    """
    from redis.exceptions import WatchError

    return WatchError


class RedisStorage(StorageBase):
    """The Redis storage implementation."""

    class KeyConfig(BaseModel):
        """Key templates for all Redis keys used by :class:`RedisStorage`.

        Nested on :class:`RedisStorage` because customising key prefixes
        is meaningful only for this backend; users tweak them via
        ``RedisStorage(key_config=RedisStorage.KeyConfig(...))``.
        """

        # Record keys
        credential: str = (
            "agentscope:user:{user_id}:credential:{credential_id}"
        )
        agent: str = "agentscope:user:{user_id}:agent:{agent_id}"
        session: str = "agentscope:user:{user_id}:session:{session_id}"

        # Index keys (Redis Sets — store all IDs for a given scope)
        credential_index: str = "agentscope:user:{user_id}:credentials"
        agent_index: str = "agentscope:user:{user_id}:agents"
        session_index: str = (
            "agentscope:user:{user_id}:agent:{agent_id}:sessions"
        )

        # Lookup key: maps (user_id, agent_id) → session_id
        session_lookup: str = (
            "agentscope:user:{user_id}:agent:{agent_id}:session"
        )

        # Message list key (Redis List — ordered message history per session)
        messages: str = (
            "agentscope:user:{user_id}:session:{session_id}:messages"
        )

        schedule: str = "agentscope:user:{user_id}:schedule:{schedule_id}"
        schedule_index: str = "agentscope:user:{user_id}:schedules"
        schedule_global_index: str = "agentscope:schedules"
        schedule_session_index: str = (
            "agentscope:user:{user_id}:schedule:{schedule_id}:sessions"
        )

        team: str = "agentscope:user:{user_id}:team:{team_id}"
        team_index: str = "agentscope:user:{user_id}:teams"

        knowledge_base: str = (
            "agentscope:user:{user_id}:knowledge_base:{knowledge_base_id}"
        )
        knowledge_base_index: str = "agentscope:user:{user_id}:knowledge_bases"

        # Knowledge document keys
        knowledge_document: str = (
            "agentscope:user:{user_id}"
            ":knowledge_base:{knowledge_base_id}"
            ":document:{document_id}"
        )
        knowledge_document_index: str = (
            "agentscope:user:{user_id}"
            ":knowledge_base:{knowledge_base_id}:documents"
        )
        # Global index of every document key as ``user_id:kb_id:doc_id``;
        # used by the lease sweeper, never by per-user listing.
        knowledge_document_global_index: str = "agentscope:knowledge_documents"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        connection_pool: ConnectionPool | None = None,
        key_ttl: int | None = None,
        key_config: "RedisStorage.KeyConfig | None" = None,
        **kwargs: Any,
    ) -> None:
        """Store connection parameters; the actual pool is created in
        :meth:`__aenter__`.

        Args:
            host (`str`, defaults to `"localhost"`): Redis server host.
            port (`int`, defaults to `6379`): Redis server port.
            db (`int`, defaults to `0`): Redis database index.
            password (`str | None`, optional): Redis password if required.
            connection_pool (`ConnectionPool | None`, optional):
                An externally managed connection pool.  When provided the pool
                is used as-is and **not** closed by :meth:`aclose` — the
                caller retains ownership of its lifecycle.  When omitted a
                pool is created from *host*/*port*/*db*/*password* on
                :meth:`__aenter__` and closed on :meth:`aclose`.
                Extra ``**kwargs`` (e.g. ``max_connections``) are forwarded to
                the pool constructor only when the pool is created internally.
            key_ttl (`int | None`, optional):
                Expire time in seconds for record keys. Refreshed on every
                write (sliding TTL). If `None`, keys do not expire.
            key_config (`RedisStorage.KeyConfig | None`, optional):
                Key template configuration. Defaults to
                ``RedisStorage.KeyConfig()``.
            **kwargs (`Any`):
                Extra keyword arguments forwarded to
                ``redis.asyncio.ConnectionPool`` when the pool is created
                internally (e.g. ``max_connections=20``, ``socket_timeout=5``).
        """
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._external_pool: ConnectionPool | None = connection_pool
        self._kwargs = kwargs
        self.key_ttl = key_ttl
        self.key_config = key_config or RedisStorage.KeyConfig()

        # Populated in __aenter__; None until the context is entered.
        self._client: Redis | None = None
        self._owned_pool: ConnectionPool | None = None

    def _key(self, template: str, **kwargs: str) -> str:
        """Format a key template with the given keyword arguments."""
        return template.format(**kwargs)

    async def _set_with_ttl(self, key: str, value: str) -> None:
        """SET a key and optionally apply the sliding TTL."""
        await self._client.set(key, value)
        await self._refresh_key_ttl(key)

    async def _refresh_key_ttl(self, key: str) -> None:
        """Apply the sliding TTL to a key, if configured."""
        if self.key_ttl is not None:
            await self._client.expire(key, self.key_ttl)

    async def __aenter__(self) -> Self:
        """Create the connection pool and Redis client.

        If an external pool was supplied at construction time it is used
        directly and its lifecycle remains the caller's responsibility.
        Otherwise, an internal pool is created from the stored host/port/db
        parameters and will be closed by :meth:`aclose`.
        """
        try:
            import redis.asyncio as aioredis
        except ImportError as e:
            raise ImportError(
                "The 'redis' package is required for RedisStorage. "
                "Install it with: pip install redis[async]",
            ) from e

        if self._external_pool is not None:
            pool = self._external_pool
        else:
            self._owned_pool = aioredis.ConnectionPool(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                decode_responses=True,
                **self._kwargs,
            )
            pool = self._owned_pool

        self._client = aioredis.Redis(connection_pool=pool)
        return self

    async def aclose(self) -> None:
        """Close the connection pool if it was created internally.

        Externally supplied pools are left open — the caller owns them.
        """
        if self._owned_pool is not None:
            await self._owned_pool.aclose()
            self._owned_pool = None
        self._client = None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Exit the async context manager."""
        await self.aclose()

    def get_client(self) -> Redis:
        """Get the underlying Redis client instance."""
        return self._client

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

    async def upsert_credential(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Create or update a credential record for the given user.

        If `credential_data.id` is set and the record already exists, the
        existing record's `data` field is updated in place (preserving
        `created_at`). If the id is set but no record exists, a new record is
        created with that id. If `credential_data.id` is ``None``, a new
        record with a generated id is always created.

        Args:
            user_id (`str`):
                The owner user id.
            credential_data (`CredentialBase`):
                Input data containing an optional `id` and the credential
                `data` dict.

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

        if credential_data.id:
            key = self._key(
                self.key_config.credential,
                user_id=user_id,
                credential_id=credential_data.id,
            )
            raw = await self._client.get(key)
            if raw:
                record = CredentialRecord.model_validate_json(raw)
                record.data = data_dump
                record.updated_at = datetime.now()
            else:
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

        key = self._key(
            self.key_config.credential,
            user_id=user_id,
            credential_id=record.id,
        )
        index_key = self._key(
            self.key_config.credential_index,
            user_id=user_id,
        )
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
        return record.id

    async def list_credentials(self, user_id: str) -> list[CredentialRecord]:
        """Return all credential records belonging to the given user.

        Reads the per-user credential index Set to obtain all ids, then
        fetches each record individually. Records whose keys have expired or
        been deleted externally are silently skipped.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[CredentialRecord]`: All credential records for the user.
        """
        index_key = self._key(
            self.key_config.credential_index,
            user_id=user_id,
        )
        ids = await self._client.smembers(index_key)
        records = []
        for cred_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.credential,
                    user_id=user_id,
                    credential_id=cred_id,
                ),
            )
            if raw:
                records.append(CredentialRecord.model_validate_json(raw))
        return records

    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord | None:
        """Fetch a single credential record by id."""
        key = self._key(
            self.key_config.credential,
            user_id=user_id,
            credential_id=credential_id,
        )
        raw = await self._client.get(key)
        return CredentialRecord.model_validate_json(raw) if raw else None

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
        key = self._key(
            self.key_config.credential,
            user_id=user_id,
            credential_id=credential_id,
        )
        index_key = self._key(
            self.key_config.credential_index,
            user_id=user_id,
        )
        deleted = await self._client.delete(key)
        await self._client.srem(index_key, credential_id)
        return deleted > 0

    async def upsert_agent(
        self,
        user_id: str,
        agent_record: AgentRecord,
    ) -> str:
        """Persist an agent record and register it in the user's agent index.

        The caller is responsible for constructing the full `AgentRecord`
        (including its `id`). If a record with the same id already exists it
        will be overwritten.

        Args:
            user_id (`str`):
                The owner user id.
            agent_record (`AgentRecord`):
                The fully-populated agent record to store.

        Returns:
            `str`:
                The id of the stored agent record.
        """
        key = self._key(
            self.key_config.agent,
            user_id=user_id,
            agent_id=agent_record.id,
        )
        index_key = self._key(self.key_config.agent_index, user_id=user_id)
        await self._set_with_ttl(key, agent_record.model_dump_json())
        await self._client.sadd(index_key, agent_record.id)
        return agent_record.id

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """Return user-facing agent records (``source='user'``).

        Reads the per-user agent index Set to obtain all ids, fetches
        each record individually, and **filters out team-spawned
        workers** (``source='team'``) — those are scoped to a team
        and only addressable via team detail / direct id lookup, not
        enumerated as part of the user's regular agent list.

        Records whose keys have expired or been deleted externally
        are silently skipped.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[AgentRecord]`:
                All ``source='user'`` agent records for the user.
        """
        index_key = self._key(self.key_config.agent_index, user_id=user_id)
        ids = await self._client.smembers(index_key)
        records = []
        for agent_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.agent,
                    user_id=user_id,
                    agent_id=agent_id,
                ),
            )
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
        """Fetch a single agent record by id."""
        key = self._key(
            self.key_config.agent,
            user_id=user_id,
            agent_id=agent_id,
        )
        raw = await self._client.get(key)
        return AgentRecord.model_validate_json(raw) if raw else None

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
        3. **Team back-references (defensive)** — if the caller chose to
           delete the agent directly instead of going through
           :meth:`delete_team`, scan the user's teams and remove the
           agent id from both the legacy :attr:`TeamData.member_ids`
           list *and* the current :attr:`TeamData.members` list. The
           normal path (``delete_team`` iterates members and calls
           ``delete_agent`` / ``delete_session`` for each) does not
           need this scan, but it keeps team records consistent if a
           caller bypasses it. Applies to invited members too — a stale
           entry pointing at a removed agent would otherwise poison
           ``TeamSay`` routing.
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
        # Cascade: sessions
        sessions = await self.list_sessions(user_id, agent_id)
        for session in sessions:
            await self.delete_session(user_id, agent_id, session.id)

        # Cascade: schedules owned by this agent
        schedules = await self.list_schedules(user_id)
        for schedule in schedules:
            if schedule.agent_id == agent_id:
                await self.delete_schedule(user_id, schedule.id)

        # Defensive: scrub agent_id from any team's member roster.
        # Covers both the legacy ``member_ids`` field and the current
        # ``members`` list (which additionally holds invited entries).
        # The common path (delete_team -> delete_agent / delete_session)
        # is unaffected because the team is being torn down anyway.
        teams = await self.list_teams(user_id)
        for team in teams:
            dirty = False
            if agent_id in team.data.member_ids:
                team.data.member_ids = [
                    mid for mid in team.data.member_ids if mid != agent_id
                ]
                dirty = True
            filtered_members = [
                m for m in team.data.members if m.agent_id != agent_id
            ]
            if len(filtered_members) != len(team.data.members):
                team.data.members = filtered_members
                dirty = True
            if dirty:
                await self.upsert_team(user_id, team)

        key = self._key(
            self.key_config.agent,
            user_id=user_id,
            agent_id=agent_id,
        )
        index_key = self._key(self.key_config.agent_index, user_id=user_id)
        deleted = await self._client.delete(key)
        await self._client.srem(index_key, agent_id)
        return deleted > 0

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
        """
        if session_id:
            key = self._key(
                self.key_config.session,
                user_id=user_id,
                session_id=session_id,
            )
            raw = await self._client.get(key)
            if raw:
                record = SessionRecord.model_validate_json(raw)
                record.config = config
                if state is not None:
                    record.state = state
                record.updated_at = datetime.now()
                await self._set_with_ttl(key, record.model_dump_json())
                return record

        # Use the caller-provided ``session_id`` when given so a
        # "create-if-missing under this id" call (e.g. scheduler's
        # stateful-mode session) lands at the expected key.
        new_id_kwargs = {"id": session_id} if session_id else {}
        record = SessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            config=config,
            source=source,
            source_schedule_id=source_schedule_id,
            state=state if state is not None else AgentState(),
            **new_id_kwargs,
        )
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=record.id,
        )
        index_key = self._key(
            self.key_config.session_index,
            user_id=user_id,
            agent_id=agent_id,
        )
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)

        if source_schedule_id:
            schedule_session_key = self._key(
                self.key_config.schedule_session_index,
                user_id=user_id,
                schedule_id=source_schedule_id,
            )
            await self._client.sadd(schedule_session_key, record.id)

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
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
        if not raw:
            raise KeyError(f"Session {session_id!r} not found.")
        record = SessionRecord.model_validate_json(raw)
        record.state = state
        record.updated_at = datetime.now()
        await self._set_with_ttl(key, record.model_dump_json())

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Return all session records for a given (user, agent) pair.

        Reads the per-agent session index Set to obtain all session ids, then
        fetches each record individually. Records whose keys have expired or
        been deleted externally are silently skipped.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id whose sessions to list.

        Returns:
            `list[SessionRecord]`: All session records for the (user, agent)
            pair.
        """
        index_key = self._key(
            self.key_config.session_index,
            user_id=user_id,
            agent_id=agent_id,
        )
        ids = await self._client.smembers(index_key)
        records = []
        for session_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.session,
                    user_id=user_id,
                    session_id=session_id,
                ),
            )
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
        """Fetch a single session record by id."""
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
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
        - **NEW**: if this session is the leader of a team (``team_id``
          set AND a :class:`TeamRecord` exists with
          ``session_id == this session_id``), call :meth:`delete_team`
          first. ``delete_team`` will recursively cascade workers and
          clear ``team_id`` on this session — that clear is idempotent
          and the session itself is deleted right after, so the order is
          safe.

        Worker sessions (``team_id`` set, but the team's
        ``leader_session_id`` is **not** this session) are deleted
        without dissolving the team — the team and the surviving leader
        keep their member_ids list pointing to the now-orphaned worker
        agent. This intentional asymmetry mirrors SQL: there is no FK
        from :class:`SessionRecord` back to the agent that owns it, so
        deleting a session doesn't automatically delete the agent.

        Args:
            user_id (`str`):
                The owner user id.
            agent_id (`str`):
                The id of the agent that owns the session (used to
                clean up the per-agent session index).
            session_id (`str`):
                The id of the session to delete.

        Returns:
            `bool`:
                ``True`` if the session existed and was deleted,
                ``False`` if no record was found.
        """
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return False

        record = SessionRecord.model_validate_json(raw)

        # Cascade: if this session leads a team, dissolve it first.
        if record.team_id:
            team = await self.get_team(user_id, record.team_id)
            if team is not None and team.session_id == session_id:
                await self.delete_team(user_id, record.team_id)

        index_key = self._key(
            self.key_config.session_index,
            user_id=user_id,
            agent_id=agent_id,
        )
        msg_key = self._key(
            self.key_config.messages,
            user_id=user_id,
            session_id=session_id,
        )
        await self._client.delete(key)
        await self._client.srem(index_key, session_id)
        await self._client.delete(msg_key)

        if record.source_schedule_id:
            schedule_session_key = self._key(
                self.key_config.schedule_session_index,
                user_id=user_id,
                schedule_id=record.source_schedule_id,
            )
            await self._client.srem(schedule_session_key, session_id)

        return True

    async def list_sessions_by_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> list[SessionRecord]:
        """Return all sessions created by a given schedule."""
        schedule_session_key = self._key(
            self.key_config.schedule_session_index,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        ids = await self._client.smembers(schedule_session_key)
        records = []
        for session_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.session,
                    user_id=user_id,
                    session_id=session_id,
                ),
            )
            if raw:
                records.append(SessionRecord.model_validate_json(raw))
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    async def upsert_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """Persist a cron task record and register it in the user and global
        indexes."""
        key = self._key(
            self.key_config.schedule,
            user_id=user_id,
            schedule_id=record.id,
        )
        index_key = self._key(self.key_config.schedule_index, user_id=user_id)
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
        await self._client.sadd(
            self.key_config.schedule_global_index,
            f"{user_id}:{record.id}",
        )
        return record.id

    async def get_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> ScheduleRecord | None:
        """Fetch a single cron task record by id."""
        key = self._key(
            self.key_config.schedule,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return None
        return ScheduleRecord.model_validate_json(raw)

    async def list_schedules(self, user_id: str) -> list[ScheduleRecord]:
        """Return all cron task records belonging to the given user."""
        index_key = self._key(
            self.key_config.schedule_index,
            user_id=user_id,
        )
        ids = await self._client.smembers(index_key)
        records = []
        for schedule_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.schedule,
                    user_id=user_id,
                    schedule_id=schedule_id,
                ),
            )
            if raw:
                records.append(ScheduleRecord.model_validate_json(raw))
        return records

    async def delete_schedule(self, user_id: str, schedule_id: str) -> bool:
        """Delete a cron task record, cascade-delete its execution sessions,
        and remove it from the user and global indexes."""
        key = self._key(
            self.key_config.schedule,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return False

        record = ScheduleRecord.model_validate_json(raw)

        # Cascade: delete all sessions created by this schedule
        sessions = await self.list_sessions_by_schedule(user_id, schedule_id)
        for session in sessions:
            await self.delete_session(
                user_id,
                record.agent_id,
                session.id,
            )

        # Clean up the schedule session index key itself
        schedule_session_key = self._key(
            self.key_config.schedule_session_index,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        await self._client.delete(schedule_session_key)

        # Delete the schedule record and its index entries
        index_key = self._key(self.key_config.schedule_index, user_id=user_id)
        await self._client.delete(key)
        await self._client.srem(index_key, schedule_id)
        await self._client.srem(
            self.key_config.schedule_global_index,
            f"{user_id}:{schedule_id}",
        )
        return True

    async def list_all_schedules(self) -> list[ScheduleRecord]:
        """Return every schedule record across all users.

        Reads the global schedule index (a Redis Set of ``user_id:schedule_id``
        pairs) and fetches each record individually.  Records whose keys have
        expired or been deleted externally are silently skipped.

        Returns:
            `list[ScheduleRecord]`: All schedule records in the store.
        """
        entries = await self._client.smembers(
            self.key_config.schedule_global_index,
        )
        records = []
        for entry in entries:
            user_id, schedule_id = entry.split(":", 1)
            raw = await self._client.get(
                self._key(
                    self.key_config.schedule,
                    user_id=user_id,
                    schedule_id=schedule_id,
                ),
            )
            if raw:
                records.append(ScheduleRecord.model_validate_json(raw))
        return records

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------

    def _message_key(self, user_id: str, session_id: str) -> str:
        """Return the Redis List key for a session's messages."""
        return self._key(
            self.key_config.messages,
            user_id=user_id,
            session_id=session_id,
        )

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        msg: Msg,
    ) -> None:
        """Persist a message to the session's message list."""
        key = self._message_key(user_id, session_id)
        last_raw = await self._client.lindex(key, -1)
        if last_raw:
            last_msg = Msg.model_validate_json(last_raw)
            if last_msg.id == msg.id:
                await self._client.lset(key, -1, msg.model_dump_json())
                await self._refresh_key_ttl(key)
                return
        await self._client.rpush(key, msg.model_dump_json())
        await self._refresh_key_ttl(key)

    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> Msg | None:
        """Fetch a single message by id from the session's message list."""
        key = self._message_key(user_id, session_id)
        length = await self._client.llen(key)
        for i in range(length - 1, -1, -1):
            raw = await self._client.lindex(key, i)
            if raw:
                msg = Msg.model_validate_json(raw)
                if msg.id == message_id:
                    return msg
        return None

    async def _find_message_index(
        self,
        key: str,
        message_id: str,
        chunk_size: int = 100,
    ) -> int | None:
        """Return the index of a message in the Redis list, or ``None``.

        Scans backwards from the tail (most recent) in chunks, since
        ``before`` cursors are typically near the end.

        Args:
            key (`str`): The Redis list key.
            message_id (`str`): The message ID to locate.
            chunk_size (`int`, optional): Number of entries fetched per
                round trip. Defaults to 100.

        Returns:
            `int | None`: Zero-based index, or ``None`` if not found.
        """
        end = await self._client.llen(key) - 1
        while end >= 0:
            start = max(end - chunk_size + 1, 0)
            raw_list = await self._client.lrange(key, start, end)
            for offset, raw in enumerate(reversed(raw_list)):
                if Msg.model_validate_json(raw).id == message_id:
                    return end - offset
            end = start - 1
        return None

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        limit: int = 50,
        before: str | None = None,
        **kwargs: Any,
    ) -> tuple[list[Msg], bool]:
        """Return the most recent messages with cursor-based pagination.

        Messages are returned in chronological order.

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
        if "offset" in kwargs:
            warnings.warn(
                "The 'offset' parameter is deprecated and will be "
                "removed in a future version. Use 'before' for "
                "cursor-based pagination instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        key = self._message_key(user_id, session_id)
        total = await self._client.llen(key)

        if total == 0:
            return [], False

        if before is None:
            end = total - 1
        else:
            idx = await self._find_message_index(key, before)
            if idx is None:
                return [], False
            end = idx - 1

        start = max(end - limit + 1, 0)
        if end < 0:
            return [], False

        raw_list = await self._client.lrange(key, start, end)
        has_more = start > 0
        return [Msg.model_validate_json(raw) for raw in raw_list], has_more

    # ------------------------------------------------------------------
    # Team persistence
    # ------------------------------------------------------------------

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
                overwritten. ``updated_at`` is refreshed to ``datetime.now()``
                before writing.

        Returns:
            `TeamRecord`:
                The stored record (with refreshed ``updated_at``).
        """
        record.updated_at = datetime.now()
        key = self._key(
            self.key_config.team,
            user_id=user_id,
            team_id=record.id,
        )
        index_key = self._key(self.key_config.team_index, user_id=user_id)
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
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
                The record, or ``None`` if no record exists at the
                ``(user_id, team_id)`` key (e.g. expired or never created).
        """
        key = self._key(
            self.key_config.team,
            user_id=user_id,
            team_id=team_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return None
        return TeamRecord.model_validate_json(raw)

    async def list_teams(self, user_id: str) -> list[TeamRecord]:
        """Return all team records belonging to the given user.

        Reads the per-user team index (a Redis Set of team ids) and fetches
        each record individually. Records whose keys have expired or been
        deleted externally are silently skipped.

        Args:
            user_id (`str`):
                The owner user id whose teams to list.

        Returns:
            `list[TeamRecord]`:
                All team records for the user, in arbitrary order (the
                index is a Set).
        """
        index_key = self._key(self.key_config.team_index, user_id=user_id)
        ids = await self._client.smembers(index_key)
        records: list[TeamRecord] = []
        for team_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.team,
                    user_id=user_id,
                    team_id=team_id,
                ),
            )
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
        allow writing ``team_id`` (which is a relation column the
        application normally only mutates via team operations).
        Idempotent: a no-op if the session does not exist or already
        holds the given value.

        Args:
            user_id (`str`):
                The owner user id.
            session_id (`str`):
                The session whose ``team_id`` should be updated.
            team_id (`str | None`):
                The new value. ``None`` detaches the session from any
                team (used by :meth:`delete_team` and by the team
                service when a session leaves a team).
        """
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return
        record = SessionRecord.model_validate_json(raw)
        if record.team_id == team_id:
            return
        record.team_id = team_id
        record.updated_at = datetime.now()
        await self._set_with_ttl(key, record.model_dump_json())

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Delete a team record and cascade-clean its members by role.

        Cascade order (mirrors what SQL's ``ON DELETE CASCADE`` would do
        for the same set of foreign keys, but role-aware to honour the
        ``AgentInvite`` semantics — see :class:`TeamMember`):

        1. Materialise the member roster via
           :func:`ensure_team_members` (which migrates legacy
           ``member_ids``-only records on first read). For each entry:

           - ``role == "created"`` — the member was spawned by
             ``AgentCreate`` and lives only for this team. Delete it in
             full via :meth:`delete_agent`, which cascades that
             worker's session.
           - ``role == "invited"`` — the member is a pre-existing
             user-owned agent that was borrowed via ``AgentInvite``.
             Only remove the team-scoped session via
             :meth:`delete_session`; the underlying
             :class:`AgentRecord` and its other sessions must survive.

        2. Clear ``team_id`` on the leader session (referenced by
           :attr:`TeamRecord.session_id`) — semantically equivalent to
           ``ON DELETE SET NULL`` for that direction of the relationship.
           Idempotent if the session has already been deleted (no-op).
        3. Delete the :class:`TeamRecord` key and remove it from the
           per-user team index.

        The cascade is best-effort: Redis has no cross-key transaction,
        so a process crash mid-cascade may leave residue. Each step is
        idempotent so retries are safe.

        Args:
            user_id (`str`):
                The owner user id.
            team_id (`str`):
                The id of the team to delete.

        Returns:
            `bool`:
                ``True`` if the team record existed and was deleted,
                ``False`` if no record was found at the
                ``(user_id, team_id)`` key.
        """
        # Local import to avoid a top-level cycle between _utils (which
        # imports TeamMember from _model) and _base (which imports the
        # abstract StorageBase from this module hierarchy).
        from ._utils import _ensure_team_members

        team = await self.get_team(user_id, team_id)
        if team is None:
            # Make sure the index is also clean if the record vanished
            # for any reason.
            index_key = self._key(self.key_config.team_index, user_id=user_id)
            await self._client.srem(index_key, team_id)
            return False

        # Role-aware member cleanup — see docstring above.
        members = await _ensure_team_members(self, user_id, team)
        for member in members:
            if member.role == "created":
                await self.delete_agent(member.owner_id, member.agent_id)
            else:  # invited
                await self.delete_session(
                    member.owner_id,
                    member.agent_id,
                    member.session_id,
                )

        # Clear team_id on the leader session (idempotent)
        await self.set_session_team_id(user_id, team.session_id, None)

        # Delete the TeamRecord key + index entry
        key = self._key(
            self.key_config.team,
            user_id=user_id,
            team_id=team_id,
        )
        existed = await self._client.delete(key)
        index_key = self._key(self.key_config.team_index, user_id=user_id)
        await self._client.srem(index_key, team_id)
        return bool(existed)

    # ------------------------------------------------------------------
    # Knowledge base persistence
    # ------------------------------------------------------------------

    async def upsert_knowledge_base(
        self,
        user_id: str,
        record: KnowledgeBaseRecord,
    ) -> KnowledgeBaseRecord:
        """Persist a knowledge base record and register it in the user index.

        If a record with the same ``id`` already exists it is overwritten
        and ``updated_at`` is refreshed; ``created_at`` is preserved.

        Args:
            user_id (`str`):
                The owner user id.
            record (`KnowledgeBaseRecord`):
                The fully-populated record to store.

        Returns:
            `KnowledgeBaseRecord`:
                The stored record (with ``updated_at`` refreshed).
        """
        if record.user_id != user_id:
            raise ValueError(
                "record.user_id does not match the given user_id.",
            )

        key = self._key(
            self.key_config.knowledge_base,
            user_id=user_id,
            knowledge_base_id=record.id,
        )
        existing_raw = await self._client.get(key)
        if existing_raw:
            existing = KnowledgeBaseRecord.model_validate_json(existing_raw)
            record.created_at = existing.created_at
        record.updated_at = datetime.now()

        index_key = self._key(
            self.key_config.knowledge_base_index,
            user_id=user_id,
        )
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
        return record

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
                The record, or ``None`` if not found.
        """
        key = self._key(
            self.key_config.knowledge_base,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )
        raw = await self._client.get(key)
        return KnowledgeBaseRecord.model_validate_json(raw) if raw else None

    async def list_knowledge_bases(
        self,
        user_id: str,
    ) -> list[KnowledgeBaseRecord]:
        """List all knowledge base records belonging to the given user.

        Reads the per-user knowledge base index Set to obtain all ids,
        then fetches each record individually. Records whose keys have
        expired or been deleted externally are silently skipped.

        Args:
            user_id (`str`):
                The owner user id.

        Returns:
            `list[KnowledgeBaseRecord]`:
                All knowledge base records for the user.
        """
        index_key = self._key(
            self.key_config.knowledge_base_index,
            user_id=user_id,
        )
        ids = await self._client.smembers(index_key)
        records: list[KnowledgeBaseRecord] = []
        for kb_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.knowledge_base,
                    user_id=user_id,
                    knowledge_base_id=kb_id,
                ),
            )
            if raw:
                records.append(
                    KnowledgeBaseRecord.model_validate_json(raw),
                )
        return records

    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> bool:
        """Delete a knowledge base record and remove it from the index.

        Cascades the per-knowledge-base document index: every
        :class:`KnowledgeDocumentRecord` indexed under the KB is
        removed from storage so no orphan documents survive the KB
        deletion.  Cleanup of the underlying vector store collection
        and blob payloads remains the caller's responsibility (the
        manager + the blob store, respectively).

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
        # Cascade: drop every document record so the sweeper does not
        # later try to redispatch leases for a KB that no longer exists.
        documents = await self.list_knowledge_documents(
            user_id,
            knowledge_base_id,
        )
        for document in documents:
            await self.delete_knowledge_document(
                user_id,
                knowledge_base_id,
                document.id,
            )

        key = self._key(
            self.key_config.knowledge_base,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )
        index_key = self._key(
            self.key_config.knowledge_base_index,
            user_id=user_id,
        )
        deleted = await self._client.delete(key)
        await self._client.srem(index_key, knowledge_base_id)
        return deleted > 0

    # ------------------------------------------------------------------
    # Knowledge document persistence
    # ------------------------------------------------------------------

    def _document_key(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> str:
        """Format the Redis key for one document record."""
        return self._key(
            self.key_config.knowledge_document,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )

    def _document_index_key(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> str:
        """Format the per-KB document index Set key."""
        return self._key(
            self.key_config.knowledge_document_index,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )

    def _document_global_token(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> str:
        """Encode (user_id, kb_id, doc_id) for the global sweeper index."""
        return f"{user_id}:{knowledge_base_id}:{document_id}"

    async def upsert_knowledge_document(
        self,
        user_id: str,
        record: KnowledgeDocumentRecord,
    ) -> KnowledgeDocumentRecord:
        """Persist a document record and update its indexes.

        If a record with the same ``id`` already exists it is
        overwritten and ``updated_at`` refreshed; ``created_at`` is
        preserved.

        Args:
            user_id (`str`):
                The owner user id.  Must match ``record.user_id``.
            record (`KnowledgeDocumentRecord`):
                The fully-populated record to persist.

        Returns:
            `KnowledgeDocumentRecord`:
                The stored record (with ``updated_at`` refreshed).
        """
        if record.user_id != user_id:
            raise ValueError(
                "record.user_id does not match the given user_id.",
            )

        key = self._document_key(
            user_id,
            record.knowledge_base_id,
            record.id,
        )
        existing_raw = await self._client.get(key)
        if existing_raw:
            existing = KnowledgeDocumentRecord.model_validate_json(
                existing_raw,
            )
            record.created_at = existing.created_at
        record.updated_at = datetime.now()

        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(
            self._document_index_key(user_id, record.knowledge_base_id),
            record.id,
        )
        await self._client.sadd(
            self.key_config.knowledge_document_global_index,
            self._document_global_token(
                user_id,
                record.knowledge_base_id,
                record.id,
            ),
        )
        return record

    async def get_knowledge_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeDocumentRecord | None:
        """Fetch a single knowledge document record by id."""
        raw = await self._client.get(
            self._document_key(user_id, knowledge_base_id, document_id),
        )
        return (
            KnowledgeDocumentRecord.model_validate_json(raw) if raw else None
        )

    async def list_knowledge_documents(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> list[KnowledgeDocumentRecord]:
        """List all documents in a knowledge base.

        Reads the per-KB document index Set and fetches each record.
        Records whose keys have expired or been deleted externally are
        silently skipped.
        """
        ids = await self._client.smembers(
            self._document_index_key(user_id, knowledge_base_id),
        )
        records: list[KnowledgeDocumentRecord] = []
        for document_id in ids:
            raw = await self._client.get(
                self._document_key(user_id, knowledge_base_id, document_id),
            )
            if raw:
                records.append(
                    KnowledgeDocumentRecord.model_validate_json(raw),
                )
        return records

    async def delete_knowledge_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> bool:
        """Delete a document record and remove it from the indexes."""
        key = self._document_key(user_id, knowledge_base_id, document_id)
        deleted = await self._client.delete(key)
        await self._client.srem(
            self._document_index_key(user_id, knowledge_base_id),
            document_id,
        )
        await self._client.srem(
            self.key_config.knowledge_document_global_index,
            self._document_global_token(
                user_id,
                knowledge_base_id,
                document_id,
            ),
        )
        return deleted > 0

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

        Reads the record, mutates the status fields in memory, and
        writes it back.  Not atomic across multiple writers — relies
        on the indexing worker holding the lease, which serialises
        status transitions for a single document.
        """
        key = self._document_key(user_id, knowledge_base_id, document_id)
        raw = await self._client.get(key)
        if not raw:
            return
        record = KnowledgeDocumentRecord.model_validate_json(raw)
        record.status = status
        if error is not None:
            record.data.error = error
        if chunk_count is not None:
            record.data.chunk_count = chunk_count
        record.updated_at = datetime.now()
        await self._set_with_ttl(key, record.model_dump_json())

    async def acquire_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
        lease_ttl: timedelta,
        now: datetime | None = None,
    ) -> bool:
        """Compare-and-swap acquisition of the processing lease.

        Implementation is a read-modify-write under a per-document
        ``WATCH`` so two workers racing on the same document cannot
        both win.  Redis ``WATCH`` aborts the transaction if the key
        changes between WATCH and EXEC; we retry a few times on
        ``WatchError`` and otherwise give up (treating the contention
        as "someone else already holds the lease").
        """
        now = now or datetime.now()
        new_deadline = now + lease_ttl

        async with self._client.pipeline(transaction=True) as pipe:
            for _ in range(3):
                try:
                    key = self._document_key(
                        user_id,
                        knowledge_base_id,
                        document_id,
                    )
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if not raw:
                        await pipe.unwatch()
                        return False
                    record = KnowledgeDocumentRecord.model_validate_json(raw)
                    holder = record.processing_node
                    deadline = record.lease_expires_at
                    if (
                        holder is not None
                        and deadline is not None
                        and deadline > now
                    ):
                        await pipe.unwatch()
                        return False
                    record.processing_node = processing_node
                    record.lease_expires_at = new_deadline
                    record.updated_at = now
                    pipe.multi()
                    pipe.set(key, record.model_dump_json())
                    if self.key_ttl is not None:
                        pipe.expire(key, self.key_ttl)
                    await pipe.execute()
                    return True
                except _watch_error():
                    # Another writer touched the key between WATCH and
                    # EXEC; loop and re-read.
                    continue
            return False

    async def renew_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
        lease_ttl: timedelta,
        now: datetime | None = None,
    ) -> bool:
        """Extend the lease this worker already holds."""
        now = now or datetime.now()
        new_deadline = now + lease_ttl

        async with self._client.pipeline(transaction=True) as pipe:
            for _ in range(3):
                try:
                    key = self._document_key(
                        user_id,
                        knowledge_base_id,
                        document_id,
                    )
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if not raw:
                        await pipe.unwatch()
                        return False
                    record = KnowledgeDocumentRecord.model_validate_json(raw)
                    if record.processing_node != processing_node:
                        await pipe.unwatch()
                        return False
                    record.lease_expires_at = new_deadline
                    record.updated_at = now
                    pipe.multi()
                    pipe.set(key, record.model_dump_json())
                    if self.key_ttl is not None:
                        pipe.expire(key, self.key_ttl)
                    await pipe.execute()
                    return True
                except _watch_error():
                    continue
            return False

    async def release_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
    ) -> None:
        """Release the lease only if this worker still owns it."""
        async with self._client.pipeline(transaction=True) as pipe:
            for _ in range(3):
                try:
                    key = self._document_key(
                        user_id,
                        knowledge_base_id,
                        document_id,
                    )
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if not raw:
                        await pipe.unwatch()
                        return
                    record = KnowledgeDocumentRecord.model_validate_json(raw)
                    if record.processing_node != processing_node:
                        await pipe.unwatch()
                        return
                    record.processing_node = None
                    record.lease_expires_at = None
                    record.updated_at = datetime.now()
                    pipe.multi()
                    pipe.set(key, record.model_dump_json())
                    if self.key_ttl is not None:
                        pipe.expire(key, self.key_ttl)
                    await pipe.execute()
                    return
                except _watch_error():
                    continue

    async def list_knowledge_documents_with_expired_lease(
        self,
        now: datetime | None = None,
    ) -> list[KnowledgeDocumentRecord]:
        """Return non-terminal documents whose lease has expired.

        Reads the global document index and filters in-memory — there
        is no Redis-side filter primitive that knows about our nested
        ``data.lease_expires_at`` field.  For production deployments
        with very large document counts a secondary index would pay
        off, but for the v1 workload the global scan is acceptable
        because the sweep runs on a slow cadence (minutes) and only
        cares about the small subset of non-terminal documents.
        """
        now = now or datetime.now()
        terminal = {"ready", "error"}
        tokens = await self._client.smembers(
            self.key_config.knowledge_document_global_index,
        )
        records: list[KnowledgeDocumentRecord] = []
        for token in tokens:
            try:
                user_id, kb_id, document_id = token.split(":", 2)
            except ValueError:
                continue
            raw = await self._client.get(
                self._document_key(user_id, kb_id, document_id),
            )
            if not raw:
                continue
            record = KnowledgeDocumentRecord.model_validate_json(raw)
            if record.status in terminal:
                continue
            if record.processing_node is None:
                continue
            if (
                record.lease_expires_at is not None
                and record.lease_expires_at < now
            ):
                records.append(record)
        return records

    async def list_knowledge_documents_pending_since(
        self,
        threshold: datetime,
    ) -> list[KnowledgeDocumentRecord]:
        """Return documents stuck in ``pending`` since before ``threshold``."""
        tokens = await self._client.smembers(
            self.key_config.knowledge_document_global_index,
        )
        records: list[KnowledgeDocumentRecord] = []
        for token in tokens:
            try:
                user_id, kb_id, document_id = token.split(":", 2)
            except ValueError:
                continue
            raw = await self._client.get(
                self._document_key(user_id, kb_id, document_id),
            )
            if not raw:
                continue
            record = KnowledgeDocumentRecord.model_validate_json(raw)
            if record.status != "pending":
                continue
            if record.created_at < threshold:
                records.append(record)
        return records
