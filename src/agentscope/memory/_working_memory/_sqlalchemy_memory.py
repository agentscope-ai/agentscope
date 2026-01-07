# -*- coding: utf-8 -*-
"""The SQLAlchemy database storage module, which supports storing messages in
a SQL database using SQLAlchemy ORM (e.g., SQLite, PostgreSQL, MySQL)."""
from typing import Any

from sqlalchemy import (
    Column,
    String,
    JSON,
    BigInteger,
    ForeignKey,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base, relationship

from ._base import MemoryBase
from ...message import Msg

Base: Any = declarative_base()


class SQLAlchemyMemoryStorage(MemoryBase):
    """The SQLAlchemy database storage class for storing messages in a SQL
    database using SQLAlchemy ORM, such as SQLite, PostgreSQL, MySQL, etc.

    .. note:: All the operations in this class are within a specific session
     and user context, identified by `session_id` and `user_id`. Cross-session
     or cross-user operations are not supported. For example, the
     `remove_messages` method will only remove messages that belong to the
     specified `session_id` and `user_id`.

    """

    class MessageTable(Base):
        """The default message table definition."""

        __tablename__ = "message"
        """The table name"""

        id = Column(String(255), primary_key=True)
        """The id column"""

        msg = Column(JSON, nullable=False)
        """The message JSON content column"""

        session = relationship(
            "SessionTable",
            back_populates="messages",
        )
        """The foreign key to the session id relationship"""

        session_id = Column(
            String(255),
            ForeignKey("session.id"),
            nullable=False,
        )
        """The foreign key to the session id"""

        index = Column(BigInteger, nullable=False, index=True)
        """The index column for ordering messages, so that we can retrieve
        messages in the order they were added."""

    class MessageMarkTable(Base):
        """The default message mark table definition."""

        __tablename__ = "message_mark"
        """The table name"""

        msg_id = Column(
            String(255),
            ForeignKey("message.id", ondelete="CASCADE"),
            primary_key=True,
        )
        """The message id column"""

        mark = Column(String(255), primary_key=True)
        """The mark column"""

    class SessionTable(Base):
        """The default session table definition."""

        __tablename__ = "session"
        """The table name"""

        id = Column(String(255), primary_key=True)
        """The session id column"""

        user = relationship("UserTable", back_populates="sessions")
        """The foreign key to the user id relationship"""

        user_id = Column(String(255), ForeignKey("users.id"), nullable=False)
        """The foreign key to the user id"""

        messages = relationship("MessageTable", back_populates="session")
        """The relationship to messages"""

    class UserTable(Base):
        """The default user table definition."""

        __tablename__ = "users"
        """The table name"""

        id = Column(String(255), primary_key=True)
        """The user id column"""

        sessions = relationship("SessionTable", back_populates="user")
        """The relationship to sessions"""

    def __init__(
        self,
        engine: AsyncEngine,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Initialize the SqlAlchemyDBStorage with a SQLAlchemy session.

        Args:
            engine (`AsyncEngine`):
                The SQLAlchemy async engine to use for database operations.
            session_id (`str | None`, optional):
                The session ID for the messages. If `None`, a default session
                ID will be used.
            user_id (`str | None`, optional):
                The user ID for the messages. If `None`, a default user ID
                will be used.

        Raises:
            `ValueError`:
                If the `engine` parameter is not an instance of
                `sqlalchemy.ext.asyncio.AsyncEngine`.
        """

        super().__init__()

        if not isinstance(engine, AsyncEngine):
            raise ValueError(
                "The 'engine' parameter must be an instance of "
                "sqlalchemy.ext.asyncio.AsyncEngine.",
            )

        self._engine = engine
        self.session_id = session_id or "default_session"
        self.user_id = user_id or "default_user"

        self._session_factory = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )
        self._db_session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        """Get the current database session, creating one if it doesn't exist.

        Returns:
            `AsyncSession`:
                The current database session.
        """
        if self._db_session is None:
            self._db_session = self._session_factory()
        return self._db_session

    async def _create_table(self) -> None:
        """Create tables in database."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Create user record if not exists
        result = await self.session.execute(
            select(self.UserTable).filter(
                self.UserTable.id == self.user_id,
            ),
        )
        user_record = result.scalar_one_or_none()

        if user_record is None:
            user_record = self.UserTable(
                id=self.user_id,
            )
            self.session.add(user_record)
            await self.session.commit()

        # Create session record if not exists
        result = await self.session.execute(
            select(self.SessionTable).filter(
                self.SessionTable.id == self.session_id,
            ),
        )
        session_record = result.scalar_one_or_none()

        if session_record is None:
            session_record = self.SessionTable(
                id=self.session_id,
                user_id=self.user_id,
            )
            self.session.add(session_record)
            await self.session.commit()

    async def get_memory(
        self,
        mark: str | None = None,
    ) -> list[Msg]:
        """Get messages from the database with the given mark (if provided).

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, retrieves all
                messages.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the database.
        """
        query = select(self.MessageTable).filter(
            self.MessageTable.session_id == self.session_id,
        )

        if mark is not None:
            query = query.join(
                self.MessageMarkTable,
                self.MessageTable.id == self.MessageMarkTable.msg_id,
            ).filter(
                self.MessageMarkTable.mark == mark,
            )

        query = query.order_by(self.MessageTable.index)

        result = await self.session.execute(query)
        results = result.scalars().all()
        return [Msg.from_dict(result.msg) for result in results]

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        mark: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add message into the storge with the given mark (if provided).

        Args:
            memories (`Msg | list[Msg] | None`):
                The message(s) to be added.
            mark (`str | list[str] | None`, optional):
                The mark to associate with the message(s). If `None`, no mark
                is associated.
        """
        if memories is None:
            return

        if not isinstance(memories, list):
            memories = [memories]

        for m in memories:
            message_record = self.MessageTable(
                id=m.id,
                msg=m.to_dict(),
                session_id=self.session_id,
                index=await self._get_next_index(),
            )
            self.session.add(message_record)

        # create mark records if mark is provided
        if mark is not None:
            for m in memories:
                mark_record = self.MessageMarkTable(
                    msg_id=m.id,
                    mark=mark,
                )
                self.session.add(mark_record)

        await self.session.commit()

    async def _get_next_index(self) -> int:
        """Get the next index for a new message in the current session.

        Returns:
            `int`:
                The next index value.
        """
        result = await self.session.execute(
            select(self.MessageTable.index)
            .filter(self.MessageTable.session_id == self.session_id)
            .order_by(self.MessageTable.index.desc())
            .limit(1),
        )
        max_index = result.scalar_one_or_none()
        return (max_index + 1) if max_index is not None else 0

    def get_db_session(self) -> AsyncSession:
        """Get the current database session.

        Example:

            .. code-block:: python
                :caption: Example of using `get_db_session`.

                storage = SqlAlchemyDBStorage(...)
                session = storage.get_db_session()
                # The database operations can be performed using the session
                result = await session.execute(
                    select(MessageTable).filter(...)
                )
                messages = result.scalars().all()


        Returns:
            `AsyncSession`:
                The current database session.
        """
        return self._db_session

    async def size(self) -> int:
        """Get the size of the messages in the storage."""
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count(self.MessageTable.id)).filter(
                self.MessageTable.session_id == self.session_id,
            ),
        )
        count = result.scalar_one()
        return count

    async def clear(self) -> None:
        """Clear all messages from the storage."""
        from sqlalchemy import delete

        # First get all message IDs in this session
        result = await self.session.execute(
            select(self.MessageTable.id).filter(
                self.MessageTable.session_id == self.session_id,
            ),
        )
        msg_ids = [row[0] for row in result.all()]

        # Delete all marks for these messages
        if msg_ids:
            await self.session.execute(
                delete(self.MessageMarkTable).filter(
                    self.MessageMarkTable.msg_id.in_(msg_ids),
                ),
            )

        # Then delete all messages
        await self.session.execute(
            delete(self.MessageTable).filter(
                self.MessageTable.session_id == self.session_id,
            ),
        )

        await self.session.commit()

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
        from sqlalchemy import delete

        if isinstance(mark, str):
            mark = [mark]

        # First, find message IDs that have the specified marks
        query = (
            select(self.MessageTable.id)
            .join(
                self.MessageMarkTable,
                self.MessageTable.id == self.MessageMarkTable.msg_id,
            )
            .filter(
                self.MessageTable.session_id == self.session_id,
                self.MessageMarkTable.mark.in_(mark),
            )
        )

        result = await self.session.execute(query)
        msg_ids = [row[0] for row in result.all()]

        if not msg_ids:
            return 0

        # Delete marks first
        await self.session.execute(
            delete(self.MessageMarkTable).filter(
                self.MessageMarkTable.msg_id.in_(msg_ids),
            ),
        )

        # Then delete the messages
        result = await self.session.execute(
            delete(self.MessageTable)
            .filter(
                self.MessageTable.session_id == self.session_id,
                self.MessageTable.id.in_(msg_ids),
            )
            .returning(self.MessageTable.id),
        )

        deleted_count = len(result.all())

        await self.session.commit()
        return deleted_count

    async def delete(
        self,
        msg_ids: list[str],
        **kwargs: Any,
    ) -> int:
        """Remove message(s) from the storage by their IDs.

        .. note:: Although MessageMarkTable has CASCADE delete on foreign key,
         we explicitly delete marks first for reliability across all database
         engines and configurations. SQLAlchemy's bulk delete bypasses
         ORM-level cascades, and SQLite requires foreign keys to be
         explicitly enabled.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        from sqlalchemy import delete

        # Delete related marks first (explicit cleanup for reliability)
        await self.session.execute(
            delete(self.MessageMarkTable).filter(
                self.MessageMarkTable.msg_id.in_(msg_ids),
            ),
        )

        # Then delete the messages
        result = await self.session.execute(
            delete(self.MessageTable)
            .filter(
                self.MessageTable.session_id == self.session_id,
                self.MessageTable.id.in_(msg_ids),
            )
            .returning(self.MessageTable.id),
        )

        deleted_count = len(result.all())

        await self.session.commit()
        return deleted_count

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

        # Type checking
        if not (isinstance(new_mark, str) or new_mark is None):
            raise ValueError(
                f"The 'new_mark' parameter must be a string or None, "
                f"but got {type(new_mark)}.",
            )

        if not (isinstance(old_mark, str) or old_mark is None):
            raise ValueError(
                f"The 'old_mark' parameter must be a string or None, "
                f"but got {type(old_mark)}.",
            )

        if msg_ids is not None and not (
            isinstance(msg_ids, list)
            and all(isinstance(_, str) for _ in msg_ids)
        ):
            raise ValueError(
                f"The 'msg_ids' parameter must be a list of strings or None, "
                f"but got {type(msg_ids)}.",
            )

        # First obtain the message ids that belong to this session
        query = select(self.MessageTable).filter(
            self.MessageTable.session_id == self.session_id,
        )

        # Filter by msg_ids if provided
        if msg_ids is not None:
            query = query.filter(self.MessageTable.id.in_(msg_ids))

        # Filter by old_mark if provided
        if old_mark is not None:
            query = query.join(
                self.MessageMarkTable,
                self.MessageTable.id == self.MessageMarkTable.msg_id,
            ).filter(self.MessageMarkTable.mark == old_mark)

        # Obtain the message records
        result = await self.session.execute(query)
        msg_ids = [str(_.id) for _ in result.scalars().all()]

        # Return early if no messages found
        if not msg_ids:
            return 0

        if new_mark:
            if old_mark:
                # Replace old_mark with new_mark
                return await self._replace_message_mark(
                    msg_ids=msg_ids,
                    old_mark=old_mark,
                    new_mark=new_mark,
                )

            # Add new_mark to the messages
            return await self._add_message_mark(
                msg_ids=msg_ids,
                mark=new_mark,
            )

        # Remove all marks from the messages
        return await self._remove_message_mark(
            msg_ids=msg_ids,
            old_mark=old_mark,
        )

    async def _replace_message_mark(
        self,
        msg_ids: list[str],
        old_mark: str,
        new_mark: str,
    ) -> int:
        """Replace the old mark with the new mark for the given messages by
        updating records in the message_mark table.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be updated.
            old_mark (`str`):
                The old mark to be replaced.
            new_mark (`str`):
                The new mark to be set.

        Returns:
            `int`:
                The number of messages updated.
        """
        from sqlalchemy import update

        await self.session.execute(
            update(self.MessageMarkTable)
            .filter(
                self.MessageMarkTable.msg_id.in_(msg_ids),
                self.MessageMarkTable.mark == old_mark,
            )
            .values(mark=new_mark),
        )
        await self.session.commit()
        return len(msg_ids)

    async def _add_message_mark(self, msg_ids: list[str], mark: str) -> int:
        """Mark the messages with the given mark by adding records to the
        message_mark table.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be marked.
            mark (`str`):
                The mark to be added to the messages.

        Returns:
            `int`:
                The number of messages marked.
        """
        for msg_id in msg_ids:
            self.session.add(
                self.MessageMarkTable(
                    msg_id=msg_id,
                    mark=mark,
                ),
            )
        await self.session.commit()
        return len(msg_ids)

    async def _remove_message_mark(
        self,
        msg_ids: list[str],
        old_mark: str | None,
    ) -> int:
        """Remove marks from the messages by deleting records from the
        message_mark table.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be unmarked.
            old_mark (`str | None`):
                The old mark to be removed. If `None`, all marks will be
                removed from the messages.

        Returns:
            `int`:
                The number of messages unmarked.
        """
        from sqlalchemy import delete

        if old_mark:
            # Remove the records with the specified old_mark and msg ID
            await self.session.execute(
                delete(self.MessageMarkTable).filter(
                    self.MessageMarkTable.msg_id.in_(msg_ids),
                    self.MessageMarkTable.mark == old_mark,
                ),
            )
        else:
            # Remove all marks for the specified msg IDs
            await self.session.execute(
                delete(self.MessageMarkTable).filter(
                    self.MessageMarkTable.msg_id.in_(msg_ids),
                ),
            )

        await self.session.commit()
        return len(msg_ids)
