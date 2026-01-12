# -*- coding: utf-8 -*-
"""The redis based memory storage implementation."""
import json
from typing import Any, TYPE_CHECKING

from ._base import MemoryBase
from ...message import Msg

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis
else:
    ConnectionPool = Any
    Redis = Any


class RedisMemory(MemoryBase):
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

    MESSAGE_KEY = "user_id:{user_id}:session:{session_id}:msg:{msg_id}"
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

    def get_client(self) -> Redis:
        """Get the underlying Redis client.

        Returns:
            `Redis`:
                The Redis client instance.
        """
        return self._client

    def _get_session_key(self) -> str:
        """Get the Redis key for the current session.

        Returns:
            `str`:
                The Redis key for storing messages in the current session.
        """
        return self.SESSION_KEY.format(
            user_id=self.user_id,
            session_id=self.session_id,
        )

    def _get_mark_key(self, mark: str) -> str:
        """Get the Redis key for a specific mark.

        Args:
            mark (`str`):
                The mark name.

        Returns:
            `str`:
                The Redis key for storing message IDs with the given mark.
        """
        return self.MARK_KEY.format(
            user_id=self.user_id,
            session_id=self.session_id,
            mark=mark,
        )

    def _get_mark_pattern(self) -> str:
        """Get the Redis key pattern for all marks in the current session.

        Returns:
            `str`:
                The Redis key pattern for all mark keys.
        """
        return self.MARK_KEY.format(
            user_id=self.user_id,
            session_id=self.session_id,
            mark="*",
        )

    def _get_message_key(self, msg_id: str) -> str:
        """Get the Redis key for a specific message.

        Args:
            msg_id (`str`):
                The message ID.

        Returns:
            `str`:
                The Redis key for storing the message data.
        """
        return self.MESSAGE_KEY.format(
            user_id=self.user_id,
            session_id=self.session_id,
            msg_id=msg_id,
        )

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get the messages from the memory by mark (if provided). Otherwise,
        get all messages.

        .. note:: If `mark` and `exclude_mark` are both provided, the messages
         will be filtered by both arguments.

        .. note:: `mark` and `exclude_mark` should not overlap.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, return all messages.
            exclude_mark (`str | None`, optional):
                The mark to exclude messages. If provided, messages with
                this mark will be excluded from the results.
            prepend_summary (`bool`, defaults to True):
                Whether to prepend the compressed summary as a message

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
            msg_ids = await self._client.lrange(self._get_session_key(), 0, -1)

        else:
            # Obtain the message IDs from the mark list
            msg_ids = await self._client.lrange(
                self._get_mark_key(mark),
                0,
                -1,
            )

        # Exclude messages by exclude_mark
        if exclude_mark:
            exclude_msg_ids = await self._client.lrange(
                self._get_mark_key(exclude_mark),
                0,
                -1,
            )
            msg_ids = [_ for _ in msg_ids if _ not in exclude_msg_ids]

        messages: list[Msg] = []
        for msg_id in msg_ids:
            msg_data = await self._client.get(self._get_message_key(msg_id))
            if msg_data is not None:
                msg_dict = json.loads(msg_data)
                messages.append(Msg.from_dict(msg_dict))

        if prepend_summary and self._compressed_summary:
            return [
                Msg(
                    "user",
                    self._compressed_summary,
                    "user",
                ),
                *messages,
            ]

        return messages

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add message into the storge with the given mark (if provided).

        Args:
            memories (`Msg | list[Msg]`):
                The message(s) to be added.
            marks (`str | list[str] | None`, optional):
                The mark(s) to associate with the message(s). If `None`, no
                mark is associated.
        """
        if memories is None:
            return

        if isinstance(memories, Msg):
            memories = [memories]

        # Normalize marks to a list
        if marks is None:
            mark_list = []
        elif isinstance(marks, str):
            mark_list = [marks]
        else:
            mark_list = marks

        # Push message ids into the session list
        await self._client.rpush(
            self._get_session_key(),
            *[m.id for m in memories],
        )

        # Store message data and marks
        for m in memories:
            # Record the message data
            await self._client.set(
                self._get_message_key(m.id),
                json.dumps(m.to_dict(), ensure_ascii=False),
            )

            # Record the marks if provided
            for mark in mark_list:
                await self._client.rpush(self._get_mark_key(mark), m.id)

    async def delete(
        self,
        msg_ids: list[str],
        **kwargs: Any,
    ) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        if not msg_ids:
            return 0

        removed_count = 0

        pipe = self._client.pipeline()
        for msg_id in msg_ids:
            # Remove from the session (0 means remove all occurrences)
            await pipe.lrem(self._get_session_key(), 0, msg_id)

            # Remove the message data
            await pipe.delete(self._get_message_key(msg_id))

            # Remove from all marks
            mark_keys = await self._client.keys(self._get_mark_pattern())
            for mark_key in mark_keys:
                await pipe.lrem(mark_key, 0, msg_id)

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
            mark_key = self._get_mark_key(m)
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
        msg_ids = await self._client.lrange(self._get_session_key(), 0, -1)

        pipe = self._client.pipeline()
        for msg_id in msg_ids:
            # Remove the message data
            await pipe.delete(self._get_message_key(msg_id))

        # Delete the session list
        await pipe.delete(self._get_session_key())

        # Delete all mark lists
        mark_keys = await self._client.keys(self._get_mark_pattern())
        for mark_key in mark_keys:
            await pipe.delete(mark_key)

        await pipe.execute()

    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
        size = await self._client.llen(self._get_session_key())
        return size

    async def update_messages_mark(
        self,
        new_mark: str | None,
        old_mark: str | None = None,
        msg_ids: list[str] | None = None,
    ) -> int:
        """A unified method to update marks of messages in the storage (add,
        remove, or change marks).

        - If `msg_ids` is provided, the update will be applied to the messages
         with the specified IDs.
        - If `old_mark` is provided, the update will be applied to the
         messages with the specified old mark. Otherwise, the `new_mark` will
         be added to all messages (or those filtered by `msg_ids`).
        - If `new_mark` is `None`, the mark will be removed from the messages.

        Args:
            new_mark (`str | None`, optional):
                The new mark to set for the messages. If `None`, the mark
                will be removed.
            old_mark (`str | None`, optional):
                The old mark to filter messages. If `None`, this constraint
                is ignored.
            msg_ids (`list[str] | None`, optional):
                The list of message IDs to be updated. If `None`, this
                constraint is ignored.

        Returns:
            `int`:
                The number of messages updated.
        """
        updated_count = 0

        # Determine which message IDs to update
        if old_mark is not None:
            # Get message IDs from the old mark list
            mark_msg_ids = await self._client.lrange(
                self._get_mark_key(old_mark),
                0,
                -1,
            )
        else:
            # Get all message IDs from the session
            mark_msg_ids = await self._client.lrange(
                self._get_session_key(),
                0,
                -1,
            )

        # Filter by msg_ids if provided
        if msg_ids is not None:
            mark_msg_ids = [mid for mid in mark_msg_ids if mid in msg_ids]

        # Update marks for each message
        for msg_id in mark_msg_ids:
            # If new_mark is None, remove the old_mark
            if new_mark is None:
                if old_mark is not None:
                    await self._client.lrem(
                        self._get_mark_key(old_mark),
                        0,
                        msg_id,
                    )
                    updated_count += 1
            else:
                # Remove from old_mark list if applicable
                if old_mark is not None:
                    await self._client.lrem(
                        self._get_mark_key(old_mark),
                        0,
                        msg_id,
                    )

                # Add to new_mark list
                new_mark_key = self._get_mark_key(new_mark)
                # Check if msg_id is already in the new mark list to avoid
                # duplicates
                existing_ids = await self._client.lrange(new_mark_key, 0, -1)
                if msg_id not in existing_ids:
                    await self._client.rpush(new_mark_key, msg_id)
                updated_count += 1

        return updated_count
