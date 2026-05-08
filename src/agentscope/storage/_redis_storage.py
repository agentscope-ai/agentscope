# -*- coding: utf-8 -*-
"""The Redis storage implementation."""
import json
from typing import Any, TYPE_CHECKING, Self

from ._base import StorageBase
from .. import logger
from ..state import AgentState

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis
else:
    ConnectionPool = Any
    Redis = Any


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
        key_template: str = (
            "agentscope:user_id:{user_id}:session:{session_id}:agent:"
            "{agent_id}:state"
        ),
        **kwargs: Any,
    ) -> None:
        """Initialize a Redis storage instance.

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
            key_ttl (`int | None`, optional):
                Expire time in seconds for each session state key. If provided,
                the expiration will be refreshed on every save/load
                (sliding TTL). If `None`, the session state will not expire.
            key_template (`str`, defaults to \
            `"agentscope:user_id:{user_id}:session:{session_id}:\
            agent:{agent_id}:state"`):
                The template for Redis keys to store agent states, which
                accepts `user_id`, `session_id`, and `agent_id`.
            **kwargs (`Any`):
                Additional keyword arguments passed to redis client.
        """
        self.key_ttl = key_ttl
        self.key_template = key_template

        try:
            import redis.asyncio as redis
        except ImportError as e:
            raise ImportError(
                "The 'redis' package is required for RedisSession. "
                "Please install it via 'pip install redis[async]'.",
            ) from e

        self._client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            connection_pool=connection_pool,
            decode_responses=True,
            **kwargs,
        )

    def _get_session_key(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
    ) -> str:
        """Generate the Redis key for a given session, agent, and user.

        Args:
            session_id (`str`):
                Redis session id.
            agent_id (`str`):
                Redis agent id.
            user_id (`str`):
                Redis user id.

        Returns:
            `str`:
                Redis session key.
        """
        return self.key_template.format(
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )

    async def get_agent_state(
        self,
        session_id: str,
        agent_id: str,
        user_id: str = "default_user",
        create_if_unexist: bool = True,
        **kwargs: Any,
    ) -> AgentState:
        """Load the agent state from the redis database.

        Args:
            session_id (`str`):
                The session id.
            agent_id (`str`):
                The agent id.
            user_id (`str`, defaults to `default_user`):
                The user id, defaults to `default_user`.
            create_if_unexist (`bool`, defaults to `True`):
                Whether to allow the case when the session state does not exist
                in Redis. If `False`, a `ValueError` will be raised when the
                session state does not exist. Otherwise, a new session state
                will be created.

        Returns:
            `AgentState`:
                The agent state.
        """
        key = self._get_session_key(session_id, user_id=user_id)

        # Use GETEX to get and refresh TTL in a single atomic operation
        if self.key_ttl is not None:
            data = await self._client.getex(key, ex=self.key_ttl)
        else:
            data = await self._client.get(key)

        if data is None:
            if create_if_unexist:
                logger.info(
                    "Session key %s does not exist in Redis. Creating a new "
                    "session state.",
                    key,
                )
                return AgentState()
            raise ValueError(
                f"Failed to load agent state because redis key {key} "
                "does not exist.",
            )

        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")

        states = json.loads(data)

        logger.info("Load session state from redis key %s successfully.", key)
        return AgentState.model_validate(states)

    async def save_agent_state(
        self,
        session_id: str,
        agent_id: str,
        agent_state: AgentState,
        user_id: str = "default_user",
        **kwargs: Any,
    ) -> None:
        """Save the agent state to the redis database.

        Args:
            session_id (`str`):
                Redis session id.
            agent_id (`str`):
                Redis agent id.
            agent_state (`AgentState`):
                The agent state.
            user_id (`str`, defaults to `default_user`):
                The user id, defaults to `default_user`.
        """

        key = self._get_session_key(session_id, agent_id, user_id)
        value = json.dumps(agent_state.model_dump(), ensure_ascii=False)

        await self._client.set(key, value, ex=self.key_ttl)

        logger.info("Save session state to redis key %s successfully.", key)

    async def close(self) -> None:
        """Close the Redis client connection."""
        await self._client.close()

    async def __aenter__(self) -> Self:
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

    def get_client(self) -> Redis:
        """Get the underlying Redis client instance.

        Returns:
            `Redis`:
                The Redis client instance.
        """
        return self._client

    async def save_mcp_config(self, mcp_name: str, config: dict) -> str:
        """Save the mcp configuration data.

        Args:
            mcp_name (`str`):
                The name of the mcp configuration.
            config (`dict`):
                The mcp configuration data.

        Returns:
            `str`:
                The saved MCP name, which maybe renamed to avoid name conflict.
        """
        # If the mcp_name already exists


        value = json.dumps(config, ensure_ascii=False)

        key = f"agentscope:mcp:config:{mcp_name}"
        await self._client.set(key, value, ex=self.key_ttl)

        return mcp_name

    async def delete_mcp_config(self, name: str) -> None:
        """Delete the mcp configuration data."""
