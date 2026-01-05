# -*- coding: utf-8 -*-
""""""
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool
else:
    ConnectionPool = "redis.asyncio.ConnectionPool"

from ._base import MemoryStorageBase
from ...message import Msg


class RedisStorageBase(MemoryStorageBase):
    """Redis storage implementation for memory storage."""


    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        connection_pool: ConnectionPool | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Redis based storage by connecting to the Redis
        server. You can provide either the connection parameters or an
        existing connection pool.

        Args:
            host (`str`, default to `"localhost"`):
                The Redis server host.
            port (`int`, default to `6379`):
                The Redis server port.
            db (`int`, default to `0`):
                The Redis database index.
            password (`str | None`, optional):
                The password for the Redis server, if required.
            connection_pool (`ConnectionPool | None`, optional):
                An optional Redis connection pool. If provided, it will be used
                instead of creating a new connection.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the Redis client.
        """
        try:
            import redis.asyncio as redis
        except ImportError as e:
            raise ImportError(
                "The 'redis' package is required for RedisStorage. "
                "Please install it via 'pip install redis[async]'."
            ) from e

        self._client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            connection_pool=connection_pool,
            **kwargs,
        )

    async def get_messages(self, mark: str | None = None) -> list[Msg]:
        """Get the messages from the storage by mark (if provided). Otherwise,
        get all messages.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, all messages are
                returned.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the storage.
        """
        import json

        if mark is None:
            # Get all message IDs from the set
            msg_ids = await self._client.smembers("agentscope:msg_ids")
            if not msg_ids:
                return []

            # Retrieve all messages
            messages = []
            for msg_id in msg_ids:
                msg_data = await self._client.get(f"agentscope:msg:{msg_id.decode()}")
                if msg_data:
                    messages.append(Msg.from_dict(json.loads(msg_data)))
            return messages

        if not isinstance(mark, str):
            raise TypeError(
                f"The mark should be a string or None, but got {type(mark)}.",
            )

        # Get message IDs associated with the mark
        msg_ids = await self._client.smembers(f"agentscope:mark:{mark}")
        if not msg_ids:
            return []

        # Retrieve messages by IDs
        messages = []
        for msg_id in msg_ids:
            msg_data = await self._client.get(f"agentscope:msg:{msg_id.decode()}")
            if msg_data:
                messages.append(Msg.from_dict(json.loads(msg_data)))
        return messages


    async def add_message(
        self,
        msg: Msg | list[Msg],
        mark: str | None = None
    ) -> None:
        """Add message into the storge with the given mark (if provided).

        Args:
            msg (`Msg | list[Msg]`):
                The message(s) to be added.
            mark (`str | None`, optional):
                The mark to associate with the message(s). If `None`, no mark
                is associated.
        """
        import json

        if isinstance(msg, Msg):
            msg = [msg]

        for m in msg:
            msg_id = m.id
            # Store the message as JSON
            await self._client.set(
                f"agentscope:msg:{msg_id}",
                json.dumps(m.to_dict()),
            )

            # Add message ID to the global set
            await self._client.sadd("agentscope:msg_ids", msg_id)

            # If mark is provided, associate the message with the mark
            if mark is not None:
                await self._client.sadd(f"agentscope:mark:{mark}", msg_id)
                # Store the reverse mapping for cleanup purposes
                await self._client.sadd(f"agentscope:msg_marks:{msg_id}", mark)

    async def remove_messages(
        self,
        msg_ids: list[int],
    ) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[int]`):
                The list of message IDs to be removed.
        """

    async def remove_messages_by_mark(
        self,
        mark: str | list[str],
    ) -> int:
        """Remove messages from the storage by their marks.

        Args:
            mark (`str | list[str]`):
                The mark(s) of the messages to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """

    async def clear(self) -> None:
        """Clear all messages from the storage."""

    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
