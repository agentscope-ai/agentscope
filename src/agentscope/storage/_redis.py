# -*- coding: utf-8 -*-
"""The Redis storage module."""
import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from ._base import StorageBase
from ._file import Session
from ..agent import AgentState
from ..message import Msg
from .._logging import logger

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis
else:
    ConnectionPool = Any
    Redis = Any


class RedisStorage(StorageBase):
    """Store session data in Redis.

    Key layout (all keys are optionally prefixed by ``key_prefix``):

    - Session data:
      ``user_id:{user_id}:session:{session_id}``  →  JSON of :class:`Session`
    - Session index (sorted set, score = created_at timestamp):
      ``user_id:{user_id}:sessions``  →  set of session_ids
    """

    _SESSION_KEY = "agentscope:user_id:{user_id}:session:{session_id}"
    _SESSION_INDEX_KEY = "agentscope:user_id:{user_id}:sessions"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        connection_pool: ConnectionPool | None = None,
        key_ttl: int | None = None,
        key_prefix: str = "",
        **kwargs: Any,
    ) -> None:
        """Initialize the Redis storage.

        Args:
            host (`str`, defaults to ``"localhost"``):
                Redis server host.
            port (`int`, defaults to ``6379``):
                Redis server port.
            db (`int`, defaults to ``0``):
                Redis database index.
            password (`str | None`, optional):
                Redis password if required.
            connection_pool (`ConnectionPool | None`, optional):
                Optional Redis connection pool.
            key_ttl (`int | None`, optional):
                Expire time in seconds for each session key. The TTL is
                refreshed (sliding) on every read/write. ``None`` means no
                expiry.
            key_prefix (`str`, defaults to ``""``):
                Optional prefix prepended to every Redis key, useful for
                isolating keys across apps/environments
                (e.g. ``"prod:"``, ``"myapp:"``).
            **kwargs:
                Additional keyword arguments forwarded to the Redis client.
        """
        try:
            import redis.asyncio as redis
        except ImportError as e:
            raise ImportError(
                "The 'redis' package is required for RedisStorage. "
                "Please install it via 'pip install redis[async]'.",
            ) from e

        self.key_ttl = key_ttl
        self.key_prefix = key_prefix or ""

        self._client: Redis = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            connection_pool=connection_pool,
            decode_responses=True,
            **kwargs,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _session_key(self, user_id: str, session_id: str) -> str:
        return self.key_prefix + self._SESSION_KEY.format(
            user_id=user_id,
            session_id=session_id,
        )

    def _index_key(self, user_id: str) -> str:
        return self.key_prefix + self._SESSION_INDEX_KEY.format(
            user_id=user_id,
        )

    async def _read_session(
        self,
        user_id: str,
        session_id: str,
    ) -> Session | None:
        key = self._session_key(user_id, session_id)
        if self.key_ttl is not None:
            data = await self._client.getex(key, ex=self.key_ttl)
        else:
            data = await self._client.get(key)
        if data is None:
            return None
        return Session.model_validate_json(data)

    async def _write_session(self, user_id: str, session: Session) -> None:
        key = self._session_key(user_id, session.session_id)
        await self._client.set(key, session.model_dump_json(), ex=self.key_ttl)
        # Keep the index in sync
        await self._client.sadd(self._index_key(user_id), session.session_id)
        logger.debug(
            "Wrote session %s to Redis key %s.",
            session.session_id,
            key,
        )

    # ------------------------------------------------------------------ #
    # History management                                                   #
    # ------------------------------------------------------------------ #

    async def get_history(
        self,
        session_id: str,
        limit: int,
        user_id: str = "default",
        **kwargs: Any,
    ) -> list[Msg]:
        session = await self._read_session(user_id, session_id)
        if session is None:
            return []
        return session.history[-limit:] if limit > 0 else session.history

    async def upsert_history(
        self,
        session_id: str,
        msgs: list[Msg],
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        session = await self._read_session(user_id, session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        existing_ids = {m.id for m in session.history}
        for msg in msgs:
            if msg.id in existing_ids:
                session.history = [
                    msg if m.id == msg.id else m for m in session.history
                ]
            else:
                session.history.append(msg)
        session.updated_at = datetime.now()
        await self._write_session(user_id, session)

    # ------------------------------------------------------------------ #
    # Agent state management                                               #
    # ------------------------------------------------------------------ #

    async def get_state(
        self,
        session_id: str,
        agent_id: str,
        user_id: str = "default",
        **kwargs: Any,
    ) -> AgentState:
        session = await self._read_session(user_id, session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        if agent_id not in session.state:
            raise KeyError(
                f"Agent {agent_id!r} not found in session {session_id!r}",
            )
        return session.state[agent_id]

    async def update_state(
        self,
        session_id: str,
        agent_id: str,
        state: AgentState,
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        session = await self._read_session(user_id, session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        session.state[agent_id] = state
        session.updated_at = datetime.now()
        await self._write_session(user_id, session)

    # ------------------------------------------------------------------ #
    # Session management                                                   #
    # ------------------------------------------------------------------ #

    async def list_sessions(
        self,
        user_id: str = "default",
        *args: Any,
        **kwargs: Any,
    ) -> list[str]:
        members = await self._client.smembers(self._index_key(user_id))
        return list(members)

    async def upsert_session(
        self,
        user_id: str = "default",
        **kwargs: Any,
    ) -> str:
        session_id = kwargs.get("session_id") or uuid.uuid4().hex
        name = kwargs.get("name")

        existing = await self._read_session(user_id, session_id)
        if existing is not None:
            if name is not None:
                existing.name = name
            existing.updated_at = datetime.now()
            await self._write_session(user_id, existing)
        else:
            session = Session(session_id=session_id, name=name)
            await self._write_session(user_id, session)
        return session_id

    async def delete_session(
        self,
        session_id: str,
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        key = self._session_key(user_id, session_id)
        await self._client.delete(key)
        await self._client.srem(self._index_key(user_id), session_id)
        logger.debug("Deleted session %s from Redis.", session_id)

    # ------------------------------------------------------------------ #
    # Connection lifecycle                                                 #
    # ------------------------------------------------------------------ #

    def get_client(self) -> Redis:
        """Return the underlying Redis client."""
        return self._client

    async def close(self) -> None:
        """Close the Redis client connection."""
        await self._client.aclose()

    async def __aenter__(self) -> "RedisStorage":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        await self.close()
