# -*- coding: utf-8 -*-
"""The Redis storage implementation."""
from datetime import datetime
from typing import Any, TYPE_CHECKING, Self

from pydantic import BaseModel

from ._base import StorageBase
from ._model import (
    AgentRecord,
    CredentialBase,
    CredentialRecord,
    ScheduleRecord,
    SessionRecord,
    WorkspaceBase,
    WorkspaceRecord,
    SessionData,
)

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis
else:
    ConnectionPool = Any
    Redis = Any


class RedisKeyConfig(BaseModel):
    """Key templates for all Redis keys used by RedisStorage."""

    # Record keys
    credential: str = "agentscope:user:{user_id}:credential:{credential_id}"
    workspace: str = "agentscope:user:{user_id}:workspace:{workspace_id}"
    agent: str = "agentscope:user:{user_id}:agent:{agent_id}"
    session: str = "agentscope:user:{user_id}:session:{session_id}"

    # Index keys (Redis Sets — store all IDs for a given scope)
    credential_index: str = "agentscope:user:{user_id}:credentials"
    workspace_index: str = "agentscope:user:{user_id}:workspaces"
    agent_index: str = "agentscope:user:{user_id}:agents"
    session_index: str = "agentscope:user:{user_id}:agent:{agent_id}:sessions"

    # Lookup key: maps (user_id, agent_id, workspace_id) → session_id
    session_lookup: str = (
        "agentscope:user:{user_id}:agent:{agent_id}"
        ":workspace:{workspace_id}:session"
    )

    schedule: str = "agentscope:user:{user_id}:schedule:{schedule_id}"
    schedule_index: str = "agentscope:user:{user_id}:schedules"


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
        if self.key_ttl is not None:
            await self._client.expire(key, self.key_ttl)

    async def __aenter__(self) -> Self:
        """Create the connection pool and Redis client.

        If an external pool was supplied at construction time it is used
        directly and its lifecycle remains the caller's responsibility.
        Otherwise an internal pool is created from the stored host/port/db
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
            user_id (`str`): The owner user id.
            credential_data (`CredentialBase`): Input data containing an
                optional `id` and the credential `data` dict.

        Returns:
            `str`: The id of the created or updated credential record.
        """
        if credential_data.id:
            key = self._key(
                self.key_config.credential,
                user_id=user_id,
                credential_id=credential_data.id,
            )
            raw = await self._client.get(key)
            if raw:
                record = CredentialRecord.model_validate_json(raw)
                record.data = credential_data.data
                record.updated_at = datetime.now()
            else:
                record = CredentialRecord(
                    id=credential_data.id,
                    user_id=user_id,
                    data=credential_data.data,
                )
        else:
            record = CredentialRecord(
                user_id=user_id,
                data=credential_data.data,
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

    async def upsert_workspace(
        self,
        user_id: str,
        workspace_data: WorkspaceBase,
    ) -> str:
        """Create or update a workspace record for the given user.

        If `workspace_data.id` is set and the record already exists, the
        existing record's `agent_id` and `data` fields are updated in place
        (preserving `created_at`). If the id is set but no record exists, a
        new record is created with that id. If `workspace_data.id` is
        ``None``, a new record with a generated id is always created.

        Args:
            user_id (`str`): The owner user id.
            workspace_data (`WorkspaceBase`): Input data containing an
                optional `id`, the `agent_id`, and the workspace `data` dict.

        Returns:
            `str`: The id of the created or updated workspace record.
        """
        if workspace_data.id:
            key = self._key(
                self.key_config.workspace,
                user_id=user_id,
                workspace_id=workspace_data.id,
            )
            raw = await self._client.get(key)
            if raw:
                record = WorkspaceRecord.model_validate_json(raw)
                record.agent_id = workspace_data.agent_id
                record.data = workspace_data.data
                record.updated_at = datetime.now()
            else:
                record = WorkspaceRecord(
                    id=workspace_data.id,
                    user_id=user_id,
                    agent_id=workspace_data.agent_id,
                    data=workspace_data.data,
                )
        else:
            record = WorkspaceRecord(
                user_id=user_id,
                agent_id=workspace_data.agent_id,
                data=workspace_data.data,
            )

        key = self._key(
            self.key_config.workspace,
            user_id=user_id,
            workspace_id=record.id,
        )
        index_key = self._key(self.key_config.workspace_index, user_id=user_id)
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
        return record.id

    async def list_workspaces(self, user_id: str) -> list[WorkspaceRecord]:
        """Return all workspace records belonging to the given user.

        Reads the per-user workspace index Set to obtain all ids, then
        fetches each record individually. Records whose keys have expired or
        been deleted externally are silently skipped.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[WorkspaceRecord]`: All workspace records for the user.
        """
        index_key = self._key(self.key_config.workspace_index, user_id=user_id)
        ids = await self._client.smembers(index_key)
        records = []
        for ws_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.workspace,
                    user_id=user_id,
                    workspace_id=ws_id,
                ),
            )
            if raw:
                records.append(WorkspaceRecord.model_validate_json(raw))
        return records

    async def delete_workspace(self, user_id: str, workspace_id: str) -> bool:
        """Delete a workspace record and remove it from the user's index.

        Args:
            user_id (`str`): The owner user id.
            workspace_id (`str`): The id of the workspace to delete.

        Returns:
            `bool`: ``True`` if the record existed and was deleted,
            ``False`` if it did not exist.
        """
        key = self._key(
            self.key_config.workspace,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        index_key = self._key(self.key_config.workspace_index, user_id=user_id)
        deleted = await self._client.delete(key)
        await self._client.srem(index_key, workspace_id)
        return deleted > 0

    async def create_agent(self, user_id: str, agent_data: AgentRecord) -> str:
        """Persist an agent record and register it in the user's agent index.

        The caller is responsible for constructing the full `AgentRecord`
        (including its `id`). If a record with the same id already exists it
        will be overwritten.

        Args:
            user_id (`str`): The owner user id.
            agent_data (`AgentRecord`): The fully-populated agent record to
                store.

        Returns:
            `str`: The id of the stored agent record.
        """
        key = self._key(
            self.key_config.agent,
            user_id=user_id,
            agent_id=agent_data.id,
        )
        index_key = self._key(self.key_config.agent_index, user_id=user_id)
        await self._set_with_ttl(key, agent_data.model_dump_json())
        await self._client.sadd(index_key, agent_data.id)
        return agent_data.id

    async def list_agent(self, user_id: str) -> list[AgentRecord]:
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

    async def get_agent(self, user_id: str, agent_id: str) -> AgentRecord | None:
        """Fetch a single agent record by id."""
        key = self._key(
            self.key_config.agent,
            user_id=user_id,
            agent_id=agent_id,
        )
        raw = await self._client.get(key)
        return AgentRecord.model_validate_json(raw) if raw else None

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete an agent record and remove it from the user's agent index.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The id of the agent to delete.

        Returns:
            `bool`: ``True`` if the record existed and was deleted,
            ``False`` if it did not exist.
        """
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
        workspace_id: str,
        session_data: SessionData,
    ) -> bool:
        """Create or update the session for a (user, agent, workspace) triple.

        A lookup key mapping ``(user_id, agent_id, workspace_id)`` to a
        ``session_id`` is maintained so that at most one session exists per
        triple. If a session already exists for the triple, its ``data``
        field is updated in place (preserving ``created_at``). Otherwise a
        new ``SessionRecord`` is created and the lookup key is written.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id this session belongs to.
            workspace_id (`str`): The workspace id this session belongs to.
            session_data (`SessionData`): The session state to persist.

        Returns:
            `bool`: Always ``True``.
        """
        lookup_key = self._key(
            self.key_config.session_lookup,
            user_id=user_id,
            agent_id=agent_id,
            workspace_id=workspace_id,
        )
        session_id = await self._client.get(lookup_key)

        if session_id:
            key = self._key(
                self.key_config.session,
                user_id=user_id,
                session_id=session_id,
            )
            raw = await self._client.get(key)
            if raw:
                record = SessionRecord.model_validate_json(raw)
                record.data = session_data
                record.updated_at = datetime.now()
                await self._set_with_ttl(key, record.model_dump_json())
                return True

        record = SessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            workspace_id=workspace_id,
            data=session_data,
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
        await self._client.set(lookup_key, record.id)
        await self._client.sadd(index_key, record.id)
        return True

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
        return records

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
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return None
        return SessionRecord.model_validate_json(raw)

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        """Delete a session record and clean up all associated keys.

        Fetches the record first to retrieve ``agent_id`` and
        ``workspace_id``, then atomically removes:

        - The session record key.
        - The session id from the per-agent session index Set.
        - The ``(user_id, agent_id, workspace_id)`` → ``session_id`` lookup
          key.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The id of the session to delete.

        Returns:
            `bool`: ``True`` if the record existed and was deleted,
            ``False`` if it did not exist.
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
        index_key = self._key(
            self.key_config.session_index,
            user_id=user_id,
            agent_id=record.agent_id,
        )
        lookup_key = self._key(
            self.key_config.session_lookup,
            user_id=user_id,
            agent_id=record.agent_id,
            workspace_id=record.workspace_id,
        )
        await self._client.delete(key)
        await self._client.srem(index_key, session_id)
        await self._client.delete(lookup_key)
        return True

    async def create_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """Persist a cron task record and register it in the user's index."""
        key = self._key(
            self.key_config.schedule,
            user_id=user_id,
            schedule_id=record.id,
        )
        index_key = self._key(
            self.key_config.schedule_index,
            user_id=user_id,
        )
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
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
        """Delete a cron task record and remove it from the user's index."""
        key = self._key(
            self.key_config.schedule,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        index_key = self._key(
            self.key_config.schedule_index,
            user_id=user_id,
        )
        deleted = await self._client.delete(key)
        await self._client.srem(index_key, schedule_id)
        return deleted > 0
