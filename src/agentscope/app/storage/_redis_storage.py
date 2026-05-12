# -*- coding: utf-8 -*-
"""The Redis storage implementation."""
from typing import Any, TYPE_CHECKING, Self

from ._base import StorageBase
from ._models import (
    AgentRecord,
    CredentialRecord,
    SessionRecord,
    WorkspaceRecord,
    SessionData,
)

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

    async def list_credentials(self, user_id: str) -> list[CredentialRecord]:
        """List all credentials for a given user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `list[CredentialRecord]`:
                List of all credentials for a given user.
        """

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

    async def upsert_workspace(
        self,
        user_id: str,
        workspace_data: WorkspaceBase,
    ) -> str:
        """Create a workspace record in the storage.

        Args:
            user_id (`str`):
                The user id.
            workspace_data (`WorkspaceBase`):
                The workspace data.

        Returns:
            `str`:
                The workspace id.
        """

    async def list_workspaces(self, user_id: str) -> list[WorkspaceRecord]:
        """List all workspaces for a given user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `list[WorkspaceRecord]`:
                List of all workspaces for a given user.
        """

    async def delete_workspace(self, user_id: str, workspace_id: str) -> bool:
        """Delete a workspace.

        Args:
            user_id (`str`):
                The user id.
            workspace_id (`str`):
                The workspace id.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """

    async def create_agent(
        self,
        user_id: str,
        agent_data: AgentRecord,
    ) -> str:
        """Create an agent record in the storage.

        Args:
            user_id (`str`):
                The user id.
            agent_data (`AgentRecord`):
                The agent data.

        Returns:
            `str`:
                The agent id.
        """

    async def list_agent(self, user_id: str) -> list[AgentRecord]:
        """List all agents for a given user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `list[AgentRecord]`:
                List of all agents for a given user.
        """

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

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        workspace_id: str,
        session_data: SessionData,
    ) -> bool:
        """Create a session record in the storage.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.
            workspace_id (`str`):
                The workspace id.
            session_data (`SessionData`):
                The session data.

        Returns:
            `bool`:
                True if updated, False if not found.
        """

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """List all sessions for a given user and agent entity.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.

        Returns:
            `list[SessionRecord]`:
                List of all sessions for a given user and agent entity.
        """

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        """Delete a session.

        Args:
            user_id (`str`):
                The user id.
            session_id (`str`):
                The session id.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """
