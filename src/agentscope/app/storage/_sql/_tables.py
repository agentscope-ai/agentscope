# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 declarative tables backing :class:`AsyncSQLAlchemyStorage`.

Every record type maps to one table with the layout:

- ``id`` primary key + ``created_at`` / ``updated_at`` timestamps;
- one column per relational / indexed field promoted from the
  record's top level;
- a single ``payload`` JSON column carrying the remainder of
  ``record.model_dump(mode="json")`` **minus** the promoted columns —
  so no field is ever stored in both places (see the design doc in
  ``_mappers.py`` for the round-trip contract).

Portability constraints — the *schema* intentionally sticks to the
subset of SQLAlchemy that works on every async-capable dialect we
target (SQLite / Postgres / MySQL): plain :class:`~sqlalchemy.JSON`
(never JSONB), no generated columns, no ``FOR UPDATE``.  Atomic
upserts *are* emitted with dialect-native ``ON CONFLICT`` /
``ON DUPLICATE KEY UPDATE`` syntax, but through the explicit
per-dialect dispatch in
:meth:`~agentscope.app.storage.AsyncSQLAlchemyStorage._upsert_stmt`
rather than leaking into the table definitions here.  The messages
table sidesteps the
JSON-record shape because it is inherently list-like — see
:class:`MessageRow` for the shape and the write path in
:class:`~agentscope.app.storage.AsyncSQLAlchemyStorage.upsert_message`.
"""
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Every id column is sized uniformly
_ID_LEN = 255


class _Base(DeclarativeBase):
    """Declarative base shared by every table in :mod:`_sql`.

    Kept private because it is an implementation detail: users of
    :class:`~agentscope.app.storage.AsyncSQLAlchemyStorage` never see it.
    """


class _JsonRecordMixin(_Base):
    """Column set common to every ``*Row`` that stores a record payload.

    Concrete tables inherit this mixin plus :class:`_Base`, add their
    own promoted columns, and set two class variables that drive the
    generic mapper in :mod:`_mappers`:

    - ``_record_cls``: the pydantic :class:`_RecordBase` subclass
      this row represents.
    - ``_indexed_fields``: the tuple of top-level record fields that
      live in dedicated columns (and therefore MUST be popped out of
      ``payload`` on write and merged back on read).

    The three envelope columns (``id`` / ``created_at`` /
    ``updated_at``) are always promoted and are handled by the mapper
    unconditionally, so ``_indexed_fields`` should list **only** the
    extra table-specific columns.
    """

    __abstract__ = True

    id: Mapped[str] = mapped_column(String(_ID_LEN), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(),
        index=True,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        index=True,
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Populated by subclasses; see class docstring.
    _record_cls: ClassVar[type]
    _indexed_fields: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def get_indexed_fields(cls) -> tuple[str, ...]:
        """Return the tuple of record fields promoted to dedicated columns."""
        return cls._indexed_fields


class CredentialRow(_JsonRecordMixin):
    """One row per :class:`~agentscope.app.storage.CredentialRecord`."""

    __tablename__ = "credentials"

    user_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )

    _indexed_fields = ("user_id",)


class VoiceProfileRow(_JsonRecordMixin):
    """One row per :class:`~agentscope.app.storage.VoiceProfileRecord`."""

    __tablename__ = "voice_profiles"

    user_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )

    _indexed_fields = ("user_id",)


class AgentRow(_JsonRecordMixin):
    """One row per :class:`~agentscope.app.storage.AgentRecord`."""

    __tablename__ = "agents"

    user_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )

    __table_args__ = (Index("ix_agents_user_source", "user_id", "source"),)

    _indexed_fields = ("user_id", "source")


class SessionRow(_JsonRecordMixin):
    """One row per :class:`~agentscope.app.storage.SessionRecord`."""

    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )
    source_schedule_id: Mapped[str | None] = mapped_column(
        String(_ID_LEN),
        nullable=True,
        index=True,
    )
    team_id: Mapped[str | None] = mapped_column(
        String(_ID_LEN),
        nullable=True,
        index=True,
    )

    __table_args__ = (Index("ix_sessions_user_agent", "user_id", "agent_id"),)

    _indexed_fields = (
        "user_id",
        "agent_id",
        "source",
        "source_schedule_id",
        "team_id",
    )


class ScheduleRow(_JsonRecordMixin):
    """One row per :class:`~agentscope.app.storage.ScheduleRecord`."""

    __tablename__ = "schedules"

    user_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )

    _indexed_fields = ("user_id", "agent_id")


class TeamRow(_JsonRecordMixin):
    """One row per :class:`~agentscope.app.storage.TeamRecord`."""

    __tablename__ = "teams"

    user_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )
    session_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
    )

    _indexed_fields = ("user_id", "session_id")


class KnowledgeBaseRow(_JsonRecordMixin):
    """One row per :class:`~agentscope.app.storage.KnowledgeBaseRecord`."""

    __tablename__ = "knowledge_bases"

    user_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )

    _indexed_fields = ("user_id",)


class KnowledgeDocumentRow(_JsonRecordMixin):
    """One row per :class:`KnowledgeDocumentRecord`.

    Promotes every lifecycle / sweeper field to a dedicated column so
    :meth:`AsyncSQLAlchemyStorage.list_knowledge_documents_with_expired_lease`
    can filter without deserialising :attr:`payload`. The composite
    ``(status, lease_expires_at)`` index serves both the expired-lease
    sweep (``WHERE status NOT IN (…) AND lease_expires_at < :now``)
    and the pending-orphan sweep (``WHERE status = 'pending' AND
    created_at < :threshold`` — covered by the plain ``status`` index
    combined with the mixin's ``created_at`` index).
    """

    __tablename__ = "knowledge_documents"

    user_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        String(_ID_LEN),
        # Documents are structurally owned by their KB — let the DB
        # cascade-delete them natively (atomic, no application loop).
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    processing_node: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_kd_status_lease",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_kd_user_kb",
            "user_id",
            "knowledge_base_id",
        ),
    )

    _indexed_fields = (
        "user_id",
        "knowledge_base_id",
        "processing_node",
        "status",
        "lease_expires_at",
    )


class MessageRow(_Base):
    """One row per persisted :class:`~agentscope.message.Msg`.

    Sits outside the ``_JsonRecordMixin`` family because messages are
    not stand-alone records — they are per-session events. A message is
    identified by the **composite** primary key ``(session_id, msg_id)``
    rather than a synthetic concatenated string: ``Msg.id`` is only
    unique *within* a session, and a composite key keeps the write path
    ("same ``(session, msg_id)`` → replace", inheriting
    :class:`RedisStorage.upsert_message`'s semantic) enforced by the DB
    without having to bound the length of a ``session_id:msg_id`` blob
    (both ids come from a user-overridable id factory).
    """

    __tablename__ = "messages"

    session_id: Mapped[str] = mapped_column(String(_ID_LEN), primary_key=True)
    msg_id: Mapped[str] = mapped_column(String(_ID_LEN), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_messages_session_created", "session_id", "created_at"),
    )
