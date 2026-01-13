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
    delete,
    update,
    func,
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


class AsyncSQLAlchemyMemory(MemoryBase):
    """The SQLAlchemy memory storage class for storing messages in a SQL
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
        """The id column, we use the f"{user_id}-{session_id}-{message_id}"
        as the primary key to ensure uniqueness across users and sessions."""

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
        engine_or_session: AsyncEngine | AsyncSession,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Initialize the SqlAlchemyDBStorage with a SQLAlchemy session.

        Args:
            engine_or_session (`AsyncEngine | AsyncSession`):
                The SQLAlchemy asynchronous engine or session to use for
                database operations. If you're using a connection pool, maybe
                you want to pass in an `AsyncSession` instance.
            session_id (`str | None`, optional):
                The session ID for the messages. If `None`, a default session
                ID will be used.
            user_id (`str | None`, optional):
                The user ID for the messages. If `None`, a default user ID
                will be used.

        Raises:
            `ValueError`:
                If the `engine` parameter is not an instance of
                `sqlalchemy.ext.asyncio.AsyncEngine` or `sqlalchemy.
                ext.asyncio.AsyncSession`.
        """
        super().__init__()

        self._db_session: AsyncSession | None = None

        if isinstance(engine_or_session, AsyncEngine):
            self._session_factory = async_sessionmaker(
                bind=engine_or_session,
                expire_on_commit=False,
            )

        elif isinstance(engine_or_session, AsyncSession):
            self._session_factory = None
            self._db_session = engine_or_session

        else:
            raise ValueError(
                "The 'engine_or_session' parameter must be an instance of "
                "sqlalchemy.ext.asyncio.AsyncEngine.",
            )

        self.session_id = session_id or "default_session"
        self.user_id = user_id or "default_user"

        # Flag to track if tables and records have been initialized
        self._initialized = False

    def _make_message_id(self, msg_id: str) -> str:
        """Generate a composite primary key for a message.

        Args:
            msg_id (`str`):
                The original message ID.

        Returns:
            `str`:
                The composite primary key in the format
                "{user_id}-{session_id}-{message_id}".
        """
        return f"{self.user_id}-{self.session_id}-{msg_id}"

    @property
    def session(self) -> AsyncSession:
        """Get the current database session, creating one if it doesn't exist.

        Returns:
            `AsyncSession`:
                The current database session.

        Note:
            - If an external session was provided, it will be returned as-is
            - If using internal session factory, a new session will be created
              if the current one is None or inactive, and _initialized flag
              will be reset to ensure proper re-initialization
        """
        # External session: return as-is (managed by caller)
        if self._session_factory is None:
            return self._db_session

        # Internal session: check validity and recreate if needed
        if self._db_session is None or not self._db_session.is_active:
            self._db_session = self._session_factory()
            # Reset initialized flag when creating new session
            self._initialized = False

        return self._db_session

    async def _create_table(self) -> None:
        """Create tables in database."""
        # Skip if already initialized
        if self._initialized:
            return

        # Obtain the engine first
        engine: AsyncEngine = self.session.bind

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Track if we need to commit
        needs_commit = False

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
            needs_commit = True

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
            needs_commit = True

        # Commit once if any records were added
        if needs_commit:
            await self.session.commit()

        # Mark as initialized
        self._initialized = True

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

        Raises:
            `TypeError`:
                If the provided mark is not a string or None.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the storage.
        """
        # Type checks
        if mark is not None and not isinstance(mark, str):
            raise TypeError(
                f"The mark should be a string or None, but got {type(mark)}.",
            )

        if exclude_mark is not None and not isinstance(exclude_mark, str):
            raise TypeError(
                f"The exclude_mark should be a string or None, but got "
                f"{type(exclude_mark)}.",
            )

        await self._create_table()

        # Step 1: First filter by session_id to narrow down the dataset
        # This ensures the database uses the session_id index first
        base_query = select(self.MessageTable).filter(
            self.MessageTable.session_id == self.session_id,
        )

        # Step 2: Apply mark filtering if provided
        if mark:
            # Join with mark table only on the session-filtered messages
            base_query = base_query.join(
                self.MessageMarkTable,
                self.MessageTable.id == self.MessageMarkTable.msg_id,
            ).filter(
                self.MessageMarkTable.mark == mark,
            )

        # Step 3: Apply exclude_mark filtering if provided
        if exclude_mark:
            # Use a subquery to find message IDs with the exclude_mark
            # within the current session only
            exclude_subquery = (
                select(self.MessageMarkTable.msg_id)
                .filter(
                    self.MessageMarkTable.msg_id.in_(
                        select(self.MessageTable.id).filter(
                            self.MessageTable.session_id == self.session_id,
                        ),
                    ),
                    self.MessageMarkTable.mark == exclude_mark,
                )
                .scalar_subquery()
            )
            # Exclude messages whose IDs are in the subquery
            base_query = base_query.filter(
                self.MessageTable.id.notin_(exclude_subquery),
            )

        # Step 4: Order by index to maintain message order
        query = base_query.order_by(self.MessageTable.index)

        result = await self.session.execute(query)
        results = result.scalars().all()

        msgs = [Msg.from_dict(result.msg) for result in results]
        if prepend_summary and self._compressed_summary:
            return [
                Msg(
                    "user",
                    self._compressed_summary,
                    "user",
                ),
                *msgs,
            ]

        return msgs

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        skip_duplicated: bool = True,
        **kwargs: Any,
    ) -> None:
        """Add message into the storage with the given mark (if provided).

        Args:
            memories (`Msg | list[Msg] | None`):
                The message(s) to be added.
            marks (`str | list[str] | None`, optional):
                The mark(s) to associate with the message(s). If `None`, no
                mark is associated.
            skip_duplicated (`bool`, defaults to `True`):
                If `True`, skip messages with duplicate IDs that already exist
                in the storage. If `False`, raise an `IntegrityError` when
                attempting to add a message with an existing ID.

        Raises:
            `IntegrityError`:
                If a message with the same ID already exists in the storage
                and `skip_duplicated` is set to `False`.
        """
        if memories is None:
            return

        # Type checking
        if isinstance(memories, Msg):
            memories = [memories]
        elif not (
            isinstance(memories, list)
            and all(isinstance(_, Msg) for _ in memories)
        ):
            raise TypeError(
                "The 'memories' parameter must be a Msg instance or a list of "
                f"Msg instances, but got {type(memories)}.",
            )

        if isinstance(marks, str):
            marks = [marks]
        elif marks is not None and not (
            isinstance(marks, list) and all(isinstance(m, str) for m in marks)
        ):
            raise TypeError(
                "The 'marks' parameter must be a string or a list of strings, "
                f"but got {type(marks)}.",
            )

        # Create table if not exists
        await self._create_table()

        # If skip_duplicated is True, filter out existing messages
        messages_to_add = memories
        if skip_duplicated:
            existing_msg_ids = set()
            result = await self.session.execute(
                select(self.MessageTable.id).filter(
                    self.MessageTable.id.in_(
                        [self._make_message_id(m.id) for m in memories],
                    ),
                ),
            )
            existing_msg_ids = {row[0] for row in result.fetchall()}

            messages_to_add = [
                m
                for m in memories
                if self._make_message_id(m.id) not in existing_msg_ids
            ]

            # If all messages are duplicates, return early
            if not messages_to_add:
                return

        # Get the starting index once to avoid race conditions
        start_index = await self._get_next_index()

        # Add messages to message table
        for i, m in enumerate(messages_to_add):
            message_record = self.MessageTable(
                id=self._make_message_id(m.id),
                msg=m.to_dict(),
                session_id=self.session_id,
                index=start_index + i,
            )
            self.session.add(message_record)

        # Create mark records if marks are provided (use bulk insert)
        if marks:
            mark_records = [
                {"msg_id": self._make_message_id(msg.id), "mark": mark}
                for msg in messages_to_add
                for mark in marks
            ]
            if mark_records:
                if skip_duplicated:
                    # Query existing mark combinations to avoid duplicates
                    result = await self.session.execute(
                        select(
                            self.MessageMarkTable.msg_id,
                            self.MessageMarkTable.mark,
                        ),
                    )
                    existing_marks = {
                        (row[0], row[1]) for row in result.fetchall()
                    }

                    # Filter out existing mark combinations
                    mark_records = [
                        r
                        for r in mark_records
                        if (r["msg_id"], r["mark"]) not in existing_marks
                    ]

                if mark_records:
                    await self.session.run_sync(
                        lambda session: session.bulk_insert_mappings(
                            self.MessageMarkTable,
                            mark_records,
                        ),
                    )

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

    async def size(self) -> int:
        """Get the size of the messages in the storage."""
        result = await self.session.execute(
            select(func.count(self.MessageTable.id)).filter(
                self.MessageTable.session_id == self.session_id,
            ),
        )
        return result.scalar_one()

    async def clear(self) -> None:
        """Clear all messages from the storage."""
        # Delete all marks for messages in this session
        await self.session.execute(
            delete(self.MessageMarkTable).where(
                self.MessageMarkTable.msg_id.in_(
                    select(self.MessageTable.id).filter(
                        self.MessageTable.session_id == self.session_id,
                    ),
                ),
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

        # Store the count before deletion
        deleted_count = len(msg_ids)

        # Delete marks first
        await self.session.execute(
            delete(self.MessageMarkTable).filter(
                self.MessageMarkTable.msg_id.in_(msg_ids),
            ),
        )

        # Then delete the messages
        await self.session.execute(
            delete(self.MessageTable).filter(
                self.MessageTable.session_id == self.session_id,
                self.MessageTable.id.in_(msg_ids),
            ),
        )

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
        # Convert to composite keys
        composite_ids = [self._make_message_id(msg_id) for msg_id in msg_ids]

        if not composite_ids:
            return 0

        # Store the count before deletion
        deleted_count = len(composite_ids)

        # Delete related marks first (explicit cleanup for reliability)
        await self.session.execute(
            delete(self.MessageMarkTable).filter(
                self.MessageMarkTable.msg_id.in_(composite_ids),
            ),
        )

        # Then delete the messages
        await self.session.execute(
            delete(self.MessageTable).filter(
                self.MessageTable.session_id == self.session_id,
                self.MessageTable.id.in_(composite_ids),
            ),
        )

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
        if new_mark is not None and not isinstance(new_mark, str):
            raise ValueError(
                f"The 'new_mark' parameter must be a string or None, "
                f"but got {type(new_mark)}.",
            )

        if old_mark is not None and not isinstance(old_mark, str):
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
            # Convert to composite keys
            composite_ids = [
                self._make_message_id(msg_id) for msg_id in msg_ids
            ]
            query = query.filter(self.MessageTable.id.in_(composite_ids))

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
        # Use bulk insert for better performance
        mark_records = [{"msg_id": msg_id, "mark": mark} for msg_id in msg_ids]

        if mark_records:
            await self.session.run_sync(
                lambda session: session.bulk_insert_mappings(
                    self.MessageMarkTable,
                    mark_records,
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
        delete_query = delete(self.MessageMarkTable).filter(
            self.MessageMarkTable.msg_id.in_(msg_ids),
        )

        if old_mark:
            delete_query = delete_query.filter(
                self.MessageMarkTable.mark == old_mark,
            )

        await self.session.execute(delete_query)
        await self.session.commit()
        return len(msg_ids)

    async def close(self) -> None:
        """Close the database session."""
        if self._db_session and self._db_session.is_active:
            await self._db_session.close()

        self._db_session = None
        self._initialized = False

    async def __aenter__(self) -> "AsyncSQLAlchemyMemory":
        """Enter the async context manager.

        Returns:
            `AsyncSQLAlchemyMemory`:
                The memory instance itself.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Exit the async context manager and close the session.

        Args:
            exc_type (`type[BaseException] | None`):
                The exception type if an exception was raised.
            exc_value (`BaseException | None`):
                The exception instance if an exception was raised.
            traceback (`Any`):
                The traceback object if an exception was raised.
        """
        await self.close()
