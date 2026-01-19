# -*- coding: utf-8 -*-
"""The Redis session class."""
import json
from typing import Any, TYPE_CHECKING

from ._session_base import SessionBase
from .._logging import logger
from ..module import StateModule

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis
else:
    ConnectionPool = Any
    Redis = Any


class RedisSession(SessionBase):
    """The Redis session class."""

    SESSION_KEY = "agentscope:session:{session_id}:state"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        connection_pool: ConnectionPool | None = None,
        ttl: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Redis session class by connecting to Redis.

        Args:
            host (`str`, defaults to `"localhost"`):
                Redis server host.
            port (`int`, defaults to `6379`):
                Redis server port.
            db (`int`, defaults to `0`):
                Redis database index.
            password (`str | None`, optional):
                Redis password if required.
            connection_pool (`ConnectionPool | None`, optional):
                Optional Redis connection pool.
            ttl (`int | None`, optional):
                Expire time in seconds for each session state key. If `None`,
                the session state will not expire.
            **kwargs (`Any`):
                Additional keyword arguments passed to redis client.
        """
        try:
            import redis.asyncio as redis
        except ImportError as e:
            raise ImportError(
                "The 'redis' package is required for RedisSession. "
                "Please install it via 'pip install redis[async]'.",
            ) from e

        self.ttl = ttl

        self._client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            connection_pool=connection_pool,
            **kwargs,
        )

    def get_client(self) -> Redis:
        """Get the underlying Redis client.

        Returns:
            `Redis`:
                The Redis client instance.
        """
        return self._client

    def _get_session_key(self, session_id: str) -> str:
        """Get the Redis key to store a given session state."""
        return self.SESSION_KEY.format(
            session_id=session_id,
        )

    async def save_session_state(
        self,
        session_id: str,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Save the state dictionary to Redis.

        Args:
            session_id (`str`):
                The session id.
            **state_modules_mapping (`dict[str, StateModule]`):
                A dictionary mapping of state module names to their instances.
        """
        state_dicts = {
            name: state_module.state_dict()
            for name, state_module in state_modules_mapping.items()
        }

        key = self._get_session_key(session_id)
        value = json.dumps(state_dicts, ensure_ascii=False)

        if self.ttl is None:
            await self._client.set(key, value)
        else:
            await self._client.setex(key, self.ttl, value)

        logger.info("Save session state to redis key %s successfully.", key)

    async def load_session_state(
        self,
        session_id: str,
        allow_not_exist: bool = True,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the state dictionary from Redis.

        Args:
            session_id (`str`):
                The session id.
            allow_not_exist (`bool`, defaults to `True`):
                Whether to allow the session to not exist.
            **state_modules_mapping (`dict[str, StateModule]`):
                The mapping of state modules to be loaded.
        """
        key = self._get_session_key(session_id)
        data = await self._client.get(key)

        if data is None:
            if allow_not_exist:
                logger.info(
                    "Session key %s does not exist in Redis. Skip loading "
                    "session state.",
                    key,
                )
                return
            raise ValueError(
                f"Failed to load session state because redis key {key} "
                "does not exist.",
            )

        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")

        states = json.loads(data)

        for name, state_module in state_modules_mapping.items():
            if name in states:
                state_module.load_state_dict(states[name])

        logger.info("Load session state from redis key %s successfully.", key)

    async def delete_session_state(self, session_id: str) -> int:
        """Delete a session state in Redis.

        Args:
            session_id (`str`):
                The session id.

        Returns:
            `int`:
                Number of keys deleted (0 or 1).
        """
        key = self._get_session_key(session_id)
        return int(await self._client.delete(key))

    async def close(self) -> None:
        """Close the Redis client connection."""
        await self._client.close()

    async def __aenter__(self) -> "RedisSession":
        """Enter the async context manager.

        Returns:
            `RedisSession`:
                The current `RedisSession` instance.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Exit the async context manager and close the connection.

        Args:
            exc_type (`type[BaseException] | None`):
                The type of the exception.
            exc_value (`BaseException | None`):
                The exception instance.
            traceback (`Any`):
                The traceback.
        """
        await self.close()
