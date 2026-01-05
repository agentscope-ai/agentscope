# -*- coding: utf-8 -*-
"""The SQLAlchemy database storage module, which supports storing messages in
a SQL database using SQLAlchemy ORM (e.g., SQLite, PostgreSQL, MySQL)."""
from sqlalchemy import Column, String, JSON, Engine, BigInteger, ForeignKey
from sqlalchemy.orm import (
    declarative_base,
    relationship,
    Session,
    sessionmaker,
)

from ._base import MemoryStorageBase
from ...message import Msg

Base = declarative_base()


class SqlAlchemyDBStorageBase(MemoryStorageBase):
    """The SQLAlchemy database storage class for storing messages in a SQL
    database using SQLAlchemy ORM, such as SQLite, PostgreSQL, MySQL, etc.
    """

    class MessageTable(Base):
        """The message table definition."""

        __tablename__ = "message"
        """The table name"""

        id = Column(String(255), primary_key=True)
        """The id column"""

        mark = Column(String(255), nullable=True)
        """The mark column"""

        msg = Column(JSON, nullable=False)
        """The message JSON content column"""

        session_id = relationship(
            "SessionTable",
            back_populates="messages",
        )
        """The foreign key to the session id relationship"""

        session = Column(String(255), ForeignKey("session.id"), nullable=False)
        """The foreign key to the session id"""

        index = Column(BigInteger, nullable=False, index=True)
        """The index column for ordering messages, so that we can retrieve
        messages in the order they were added."""

    class SessionTable(Base):
        """The session table definition."""

        __tablename__ = "session"
        """The table name"""

        id = Column(String(255), primary_key=True)
        """The session id column"""

        user = relationship("UserTable", back_populates="sessions")
        """The foreign key to the user id relationship"""

        user_id = Column(String(255), ForeignKey("users.id"), nullable=False)
        """The foreign key to the user id"""

    class UserTable(Base):
        """The user table definition."""

        __tablename__ = "users"
        """The table name"""

        id = Column(String(255), primary_key=True)
        """The user id column"""

    def __init__(
        self,
        engine: Engine,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Initialize the SqlAlchemyDBStorage with a SQLAlchemy session.

        Args:
            engine (`Engine`):
                The SQLAlchemy engine to use for database operations.
            session_id (`str | None`, optional):
                The session ID for the messages. If `None`, a default session
                ID will be used.
            user_id (`str | None`, optional):
                The user ID for the messages. If `None`, a default user ID
                will be used.

        Raises:
            `ValueError`:
                If the `engine` parameter is not an instance of
                `sqlalchemy.Engine`.
        """

        if not isinstance(engine, Engine):
            raise ValueError(
                "The 'engine' parameter must be an instance of "
                "sqlalchemy.Engine.",
            )

        self._engine = engine
        self.session_id = session_id or "default_session"
        self.user_id = user_id or "default_user"

        self._session_factory = sessionmaker(bind=engine)
        self._session: Session | None = None

    @property
    def session(self) -> Session:
        """Get the current database session, creating one if it doesn't exist.

        Returns:
            `Session`:
                The current database session.
        """
        if self._session is None:
            self._session = self._session_factory()
        return self._session

    def _create_table(self) -> None:
        """Create tables in database."""
        Base.metadata.create_all(self._engine)

        # Create user record if not exists
        user_record = (
            self.session.query(self.UserTable)
            .filter(
                self.UserTable.id == self.user_id,
            )
            .first()
        )
        if user_record is None:
            user_record = self.UserTable(
                id=self.user_id,
            )
            self.session.add(user_record)
            self.session.commit()

        # Create session record if not exists
        session_record = (
            self.session.query(self.SessionTable)
            .filter(
                self.SessionTable.id == self.session_id,
            )
            .first()
        )
        if session_record is None:
            session_record = self.SessionTable(
                id=self.session_id,
                user_id=self.user_id,
            )
            self.session.add(session_record)
            self.session.commit()

    def get_messages(
        self,
        mark: str | None = None,
    ) -> list[Msg]:
        """Get messages from the database.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, retrieves all
                messages.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the database.
        """
        query = self.session.query(self.MessageTable).filter(
            self.MessageTable.session_id == self.session_id,
        )

        if mark is not None:
            query = query.filter(self.MessageTable.mark == mark)

        query = query.order_by(self.MessageTable.index)

        results = query.all()
        return [Msg.from_dict(result.msg) for result in results]

    async def add_message(
        self, msg: Msg | list[Msg], mark: str | None = None
    ) -> None:
        """Add message into the storge with the given mark (if provided).

        Args:
            msg (`Msg | list[Msg]`):
                The message(s) to be added.
            mark (`str | None`, optional):
                The mark to associate with the message(s). If `None`, no mark
                is associated.
        """
        if not isinstance(msg, list):
            msg = [msg]

        for m in msg:
            message_record = self.MessageTable(
                id=m.id,
                mark=mark,
                msg=m.to_dict(),
            )
            self.session.add(message_record)

        self.session.commit()

    def get_db_session(self) -> Session:
        """Get the current database session.

        Example:

            .. code-block:: python
                :caption: Example of using `get_db_session`.

                storage = SqlAlchemyDBStorage(...)
                session = storage.get_db_session()
                # The database operations can be performed using the session
                message = session.query(MessageTable).filter(...).all()


        Returns:
            `Session`:
                The current database session.
        """
        return self._session

    async def size(self) -> int:
        """Get the size of the messages in the storage."""
        query = self.session.query(self.MessageTable).filter(
            self.MessageTable.session_id == self.session_id,
        )
        count = query.count()
        return count

    async def clear(self) -> None:
        """Clear all messages from the storage."""
        query = self.session.query(self.MessageTable).filter(
            self.MessageTable.session_id == self.session_id,
        )
        query.delete()
        self.session.commit()

    async def remove_messages_by_mark(self, mark: str | list[str]) -> int:
        """Remove messages from the storage by their marks.

        Args:
            mark (`str | list[str]`):
                The mark(s) of the messages to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        query = self.session.query(self.MessageTable).filter(
            self.MessageTable.session_id == self.session_id,
        )

        if isinstance(mark, str):
            mark = [mark]

        query = query.filter(self.MessageTable.mark.in_(mark))
        deleted_count = query.delete(synchronize_session=False)
        self.session.commit()
        return deleted_count

    async def remove_messages(self, msg_ids: list[int]) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[int]`):
                The list of message IDs to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        query = self.session.query(self.MessageTable).filter(
            self.MessageTable.session_id == self.session_id,
        )

        query = query.filter(self.MessageTable.id.in_(msg_ids))
        deleted_count = query.delete(synchronize_session=False)
        self.session.commit()
        return deleted_count
