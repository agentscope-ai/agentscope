# -*- coding: utf-8 -*-
"""The redis based memory storage implementation."""
import json
from typing import Any, TYPE_CHECKING

from ._base import MemoryBase
from ...message import Msg

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool
else:
    ConnectionPool = Any


class RedisStorageBase(MemoryBase):
    """Redis memory storage implementation, which supports session and user
    context.

    .. note:: All the operations in this class are within a specific session
     and user context, identified by `session_id` and `user_id`. Cross-session
     or cross-user operations are not supported. For example, the
     `remove_messages` method will only remove messages that belong to the
     specified `session_id` and `user_id`.

    """

    SESSION_KEY = "user_id:{user_id}:session:{session_id}:messages"
    """The Redis key pattern to store messages for a specific session."""

    MARK_KEY = "user_id:{user_id}:session:{session_id}:mark:{mark}"
    """The Redis key pattern to store message ids that belong to a specific
    mark."""

    MESSAGE_KEY = "user_id:{user_id}:msg:{msg_id}"
    """The Redis key pattern for storing message data."""

    def __init__(
        self,
        session_id: str = "default_session",
        user_id: str = "default_user",
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
            session_id (`str`, default to `"default_session"`):
                The session ID for the storage.
            user_id (`str`, default to `"default_user"`):
                The user ID for the storage.
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
                "Please install it via 'pip install redis[async]'.",
            ) from e

        super().__init__()

        self.session_id = session_id
        self.user_id = user_id

        self._client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            connection_pool=connection_pool,
            **kwargs,
        )

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get the messages from the memory by mark (if provided). Otherwise,
        get all messages.

        .. note:: If provided a list of strings as `mark` or `exclude_mark`,
         these marks will be treated as an OR condition.

        .. note:: `mark` and `exclude_mark` should not overlap.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, return all messages.
            exclude_mark (`str | None`, optional):
                The mark to exclude messages. If provided, messages with
                this mark will be excluded from the results.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the storage.
        """
        # Type checks
        if not (mark is None or isinstance(mark, str)):
            raise TypeError(
                f"The mark should be a string or None, but got {type(mark)}.",
            )

        if not (exclude_mark is None or isinstance(exclude_mark, str)):
            raise TypeError(
                f"The exclude_mark should be a string or None, but got "
                f"{type(exclude_mark)}.",
            )

        if mark is None:
            # Obtain the message IDs from the session list
            mark_key = self.MARK_KEY.format(
                session_id=self.session_id,
                mark=mark,
            )
            msg_ids = await self._client.lrange(mark_key, 0, -1)

        else:
            # Obtain the message IDs from the session list directly
            session_key = self.SESSION_KEY.format(session_id=self.session_id)
            msg_ids = await self._client.lrange(session_key, 0, -1)

        # Exclude messages by exclude_mark
        if exclude_mark:
            exclude_mark_key = self.MARK_KEY.format(
                session_id=self.session_id,
                mark=exclude_mark,
            )
            exclude_msg_ids = await self._client.lrange(
                exclude_mark_key,
                0,
                -1,
            )
            msg_ids = [_ for _ in msg_ids if _ not in exclude_msg_ids]

        messages: list[Msg] = []
        for msg_id in msg_ids:
            message_key = self.MESSAGE_KEY.format(msg_id=msg_id)
            msg_data = await self._client.get(message_key)
            if msg_data is not None:
                msg_dict = json.loads(msg_data, encoding="utf-8")
                messages.append(Msg.from_dict(msg_dict))

        return messages

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        mark: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add message into the storge with the given mark (if provided).

        Args:
            memories (`Msg | list[Msg]`):
                The message(s) to be added.
            mark (`str | None`, optional):
                The mark to associate with the message(s). If `None`, no mark
                is associated.
        """
        if memories is None:
            return

        if isinstance(memories, Msg):
            memories = [memories]

        # Push message ids into the session list
        session_key = self.SESSION_KEY.format(session_id=self.session_id)
        await self._client.rpush(session_key, *[m.id for m in memories])

        # Push message id into the message hash
        mark_key = self.MARK_KEY.format(session_id=self.session_id, mark=mark)

        # Store message data
        for m in memories:
            # Record the mark if provided
            if mark is not None:
                await self._client.rpush(mark_key, m.id)

            # Record the message data
            message_key = self.MESSAGE_KEY.format(msg_id=m.id)
            await self._client.set(
                message_key,
                json.dumps(m.to_dict(), ensure_ascii=False, encodings="utf-8"),
            )

    async def delete(
        self,
        msg_ids: list[str],
        **kwargs: Any,
    ) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be removed.
        """
        if not msg_ids:
            return 0

        session_key = self.SESSION_KEY.format(session_id=self.session_id)
        removed_count = 0

        pipe = self._client.pipeline()
        for msg_id in msg_ids:
            # Remove from the session
            await pipe.lrem(session_key, msg_id)

            # Remove the message data
            message_key = self.MESSAGE_KEY.format(msg_id=msg_id)
            await pipe.delete(message_key)

            # Remove from all marks
            mark_pattern = self.MARK_KEY.format(
                session_id=self.session_id,
                mark="*",
            )
            mark_keys = await self._client.keys(mark_pattern)
            for mark_key in mark_keys:
                await pipe.lrem(mark_key, msg_id)

            removed_count += 1

        await pipe.execute()
        return removed_count

    async def delete_by_mark(
        self,
        mark: str | list[str],
        **kwargs: Any,
    ) -> int:
        """Remove messages from the storage by their marks.

        Args:
            mark (`str | list[str]`):
                The mark(s) of the messages to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        if isinstance(mark, str):
            mark = [mark]

        total_removed = 0

        for m in mark:
            mark_key = self.MARK_KEY.format(session_id=self.session_id, mark=m)
            msg_ids = await self._client.lrange(mark_key, 0, -1)

            if not msg_ids:
                continue

            # Remove messages by IDs
            removed_count = await self.delete(
                msg_ids,
            )
            total_removed += removed_count

            # Delete the mark list
            await self._client.delete(mark_key)

        return total_removed

    async def clear(self) -> None:
        """Clear all messages belong to this section from the storage."""
        session_key = self.SESSION_KEY.format(session_id=self.session_id)
        msg_ids = await self._client.lrange(session_key, 0, -1)

        pipe = self._client.pipeline()
        for msg_id in msg_ids:
            # Remove the message data
            message_key = self.MESSAGE_KEY.format(msg_id=msg_id)
            await pipe.delete(message_key)

        # Delete the session list
        await pipe.delete(session_key)

        # Delete all mark lists
        mark_pattern = self.MARK_KEY.format(
            session_id=self.session_id,
            mark="*",
        )
        mark_keys = await self._client.keys(mark_pattern)
        for mark_key in mark_keys:
            await pipe.delete(mark_key)

        await pipe.execute()

    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
        session_key = self.SESSION_KEY.format(session_id=self.session_id)
        size = await self._client.llen(session_key)
        return size
