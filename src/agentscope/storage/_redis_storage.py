# -*- coding: utf-8 -*-
"""The Redis storage implementation."""
import json
from typing import Any, TYPE_CHECKING, Self

from ._base import StorageBase
from .models import MCPModel
from .. import logger
from ..app._schema._mcp import MCPBase
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

    def _get_mcp_names_key(self) -> str:
        """Generate the Redis key for storing all MCP names.

        Returns:
            `str`:
                Redis key for MCP names Set.
        """
        return "agentscope:mcps"

    def _get_mcp_config_key(self, name: str) -> str:
        """Generate the Redis key for a specific MCP configuration.

        Args:
            name (`str`):
                The MCP name.

        Returns:
            `str`:
                Redis key for the MCP configuration.
        """
        return f"agentscope:mcp:{name}"

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

    async def upsert_mcp(self, mcp_data: MCPBase) -> None:
        """Create or update an MCP configuration (upsert).

        Args:
            mcp_data (`MCPModel`):
                The MCP configuration data to be saved.
        """
        name = mcp_data.name
        names_key = self._get_mcp_names_key()
        config_key = self._get_mcp_config_key(name)

        # Add name to the Set
        await self._client.sadd(names_key, name)

        # Save the configuration (no TTL for persistent config)
        value = json.dumps(mcp_data.model_dump(), ensure_ascii=False)
        await self._client.set(config_key, value)

        logger.info("Upserted MCP config '%s' successfully.", name)

    async def list_mcps(self) -> list[MCPModel]:
        """Get all MCP configurations.

        Returns:
            `list[MCPModel]`:
                List of all MCP configurations.
        """
        names_key = self._get_mcp_names_key()

        # Get all MCP names from the Set
        names = await self._client.smembers(names_key)

        if not names:
            return []

        # Batch get all configurations using MGET
        config_keys = [self._get_mcp_config_key(name) for name in names]
        values = await self._client.mget(config_keys)

        # Parse and return MCPModel instances
        mcps = []
        for value in values:
            if value:
                if isinstance(value, (bytes, bytearray)):
                    value = value.decode("utf-8")
                mcps.append(MCPModel.model_validate(json.loads(value)))

        logger.info("Retrieved %d MCP configurations.", len(mcps))
        return mcps

    async def delete_mcp(self, name: str) -> bool:
        """Delete an MCP configuration.

        Args:
            name (`str`):
                The MCP name to delete.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """
        names_key = self._get_mcp_names_key()
        config_key = self._get_mcp_config_key(name)

        # Remove name from the Set
        removed = await self._client.srem(names_key, name)

        # Delete the configuration
        await self._client.delete(config_key)

        if removed:
            logger.info("Deleted MCP config '%s' successfully.", name)
            return True
        else:
            logger.warning("MCP config '%s' not found.", name)
            return False

    async def upsert_session_config(self) -> None:
        """"""