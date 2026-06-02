# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""The Redis storage implementation."""

from datetime import datetime
from typing import Any, TYPE_CHECKING, Self

from pydantic import BaseModel

from ._base import StorageBase
from ._model import (
    AgentRecord,
    CredentialRecord,
    ScheduleRecord,
    SessionRecord,
    SessionConfig,
    SessionSource,
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


class RedisKeyConfig(BaseModel):
    """Key templates for all Redis keys used by RedisStorage."""

    # Record keys
    credential: str = "agentscope:user:{user_id}:credential:{credential_id}"
    agent: str = "agentscope:user:{user_id}:agent:{agent_id}"
    session: str = "agentscope:user:{user_id}:session:{session_id}"

    # Index keys (Redis Sets — store all IDs for a given scope)
    credential_index: str = "agentscope:user:{user_id}:credentials"
    agent_index: str = "agentscope:user:{user_id}:agents"
    session_index: str = "agentscope:user:{user_id}:agent:{agent_id}:sessions"

    # Lookup key: maps (user_id, agent_id) → session_id
    session_lookup: str = "agentscope:user:{user_id}:agent:{agent_id}:session"

    # Message list key (Redis List — ordered message history per session)
    messages: str = "agentscope:user:{user_id}:session:{session_id}:messages"

    schedule: str = "agentscope:user:{user_id}:schedule:{schedule_id}"
    schedule_index: str = "agentscope:user:{user_id}:schedules"
    schedule_global_index: str = "agentscope:schedules"
    schedule_session_index: str = (
        "agentscope:user:{user_id}:schedule:{schedule_id}:sessions"
    )


class RedisStorage(StorageBase):
    """The Redis storage implementation."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        connection_pool: ConnectionPool | None = None,
        key_ttl: int | None = None,
        key_config: RedisKeyConfig | None = None,
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
            key_config (`RedisKeyConfig | None`, optional):
                Key template configuration. Defaults to `RedisKeyConfig()`.
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
        self.key_config = key_config or RedisKeyConfig()

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
        """Return all agent records belonging to the given user.

        Reads the per-user agent index Set to obtain all ids, then fetches
        each record individually. Records whose keys have expired or been
        deleted externally are silently skipped.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[AgentRecord]`: All agent records for the user.
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
                records.append(AgentRecord.model_validate_json(raw))
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
        """Delete an agent record and cascade-delete its sessions and
        schedules.

        Removes all session records (and their lookup / index keys) that belong
        to this agent, then removes all schedule records whose
        ``data.agent_id`` matches.  Finally, the agent record itself and its
        entry in the per-user agent index are deleted.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The id of the agent to delete.

        Returns:
            `bool`: ``True`` if the agent record existed and was deleted,
            ``False`` if it did not exist.
        """
        # Cascade: sessions
        sessions = await self.list_sessions(user_id, agent_id)
        for session in sessions:
            await self.delete_session(user_id, agent_id, session.id)

        # Cascade: schedules owned by this agent
        schedules = await self.list_schedules(user_id)
        for schedule in schedules:
            if schedule.data.agent_id == agent_id:
                await self.delete_schedule(user_id, schedule.id)

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

        record = SessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            config=config,
            source=source,
            source_schedule_id=source_schedule_id,
            state=state if state is not None else AgentState(),
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
        """Delete a session record and clean up all associated keys."""
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return False

        record = SessionRecord.model_validate_json(raw)

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

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Msg]:
        """Return messages for a session with pagination."""
        key = self._message_key(user_id, session_id)
        raw_list = await self._client.lrange(key, offset, offset + limit - 1)
        return [Msg.model_validate_json(raw) for raw in raw_list]
