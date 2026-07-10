# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""SQLAlchemy 2.0 async implementation of :class:`StorageBase`.

Talks to any dialect supported by SQLAlchemy's async engine
(SQLite / Postgres / MySQL / …) — the caller only picks the URL and
installs the matching driver.  All schema and query building goes
through dialect-neutral SA constructs; see the module doc of
:mod:`_tables` for the portability constraints we hold ourselves to.

Timestamps are stored as **naive UTC**: the backend generates every
timestamp with :func:`_utcnow` (UTC, not the machine-local
``datetime.now()``) and normalises any caller-supplied datetime with
:func:`_to_naive_utc`.  Using UTC keeps timestamps comparable across
nodes in a distributed deployment regardless of each node's local
timezone, while staying tz-naive so the plain ``DateTime`` columns
need no dialect-specific timezone handling.
"""
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Self

from .._base import StorageBase
from .._model import (
    AgentRecord,
    CredentialRecord,
    KnowledgeBaseRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentStatus,
    ScheduleRecord,
    SessionRecord,
    SessionConfig,
    SessionSource,
    TeamRecord,
)
from .._utils import _dump_with_secrets
from ._mappers import _from_record, _to_record
from ._tables import (
    _Base,
    AgentRow,
    CredentialRow,
    KnowledgeBaseRow,
    KnowledgeDocumentRow,
    MessageRow,
    ScheduleRow,
    SessionRow,
    TeamRow,
)
from ....credential import CredentialBase
from ....message import Msg
from ....state import AgentState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )


def _utcnow() -> datetime:
    """Current time as a naive UTC timestamp.

    Uses UTC rather than the machine-local ``datetime.now()`` so
    timestamps stay comparable across nodes in a distributed
    deployment; the ``tzinfo`` is stripped so the value round-trips
    through the naive ``DateTime`` columns without mixing aware/naive
    datetimes.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive_utc(dt: datetime) -> datetime:
    """Normalise *dt* to a naive UTC timestamp.

    Aware datetimes are converted to UTC; naive datetimes are assumed
    to be in the machine-local zone — the project-wide convention for
    ``datetime.now()`` — and re-anchored to UTC.  Either way the result
    is tz-naive so it matches the values produced by :func:`_utcnow`.
    """
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class SqlStorage(StorageBase):
    """Async SQLAlchemy-backed :class:`StorageBase` implementation.

    Instantiate with any SQLAlchemy async URL (the concrete driver is
    a caller-installed dependency, not part of the ``sql`` extra):

    .. code-block:: python

        storage = SqlStorage("sqlite+aiosqlite:///./as.db")
        async with storage:
            ...

    ``__aenter__`` builds the engine and (optionally) provisions the
    schema; ``aclose`` disposes the engine.
    """

    def __init__(
        self,
        url: str,
        *,
        create_tables: bool = False,
        auto_migrate: bool = False,
        engine: "AsyncEngine | None" = None,
        engine_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Configure the backend; nothing is opened until
        :meth:`__aenter__`.

        Args:
            url (`str`):
                A SQLAlchemy async URL (e.g.
                ``sqlite+aiosqlite:///./as.db``,
                ``postgresql+asyncpg://user:pw@host/db``).  Ignored
                when ``engine`` is provided.
            create_tables (`bool`, defaults to `False`):
                When `True`, run ``Base.metadata.create_all`` on the
                engine at ``__aenter__`` — convenient for tests and
                single-node dev deployments where Alembic overhead is
                unwanted.
            auto_migrate (`bool`, defaults to `False`):
                When `True`, run ``alembic upgrade head`` against the
                packaged migration scripts at ``__aenter__``.
                Convenient for single-node or dev deployments —
                every process boot brings the schema up to date.
                **Not recommended for multi-replica production**:
                two replicas racing on the same migration is unsafe.
                In that setup keep this `False` and run
                ``alembic upgrade head`` as a discrete deploy step.
            engine (`AsyncEngine | None`, optional):
                An externally managed engine.  When supplied the
                engine is used as-is and **not** disposed by
                :meth:`aclose` — the caller owns its lifecycle.  When
                omitted an engine is constructed from *url* on
                :meth:`__aenter__` and disposed on :meth:`aclose`.
            engine_kwargs (`dict[str, Any] | None`, optional):
                Extra keyword arguments forwarded to
                :func:`sqlalchemy.ext.asyncio.create_async_engine`
                when the engine is created internally (e.g.
                ``echo=True``, ``pool_size=20``).  Ignored when
                *engine* is supplied.
        """
        self._url = url
        self._create_tables = create_tables
        self._auto_migrate = auto_migrate
        self._external_engine: "AsyncEngine | None" = engine
        self._engine_kwargs = engine_kwargs or {}

        # Populated in __aenter__; None until the context is entered.
        self._engine: "AsyncEngine | None" = None
        self._owns_engine: bool = False
        self._session_factory: "async_sessionmaker[AsyncSession] | None" = None

    async def __aenter__(self) -> Self:
        """Build the engine (or adopt the external one) and optionally
        provision the schema.
        """
        from sqlalchemy.ext.asyncio import (
            async_sessionmaker,
            create_async_engine,
        )

        if self._external_engine is not None:
            self._engine = self._external_engine
            self._owns_engine = False
        else:
            self._engine = create_async_engine(
                self._url,
                future=True,
                **self._engine_kwargs,
            )
            self._owns_engine = True
        # ``expire_on_commit=False`` keeps mapper-returned rows readable
        # after commit — :class:`SqlStorage` immediately projects rows
        # back into pydantic records outside the session scope, which
        # would otherwise trigger a detached-load error.
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )

        if self._auto_migrate:
            await self._run_alembic_upgrade()

        if self._create_tables:
            async with self._engine.begin() as conn:
                await conn.run_sync(_Base.metadata.create_all)

        return self

    async def _run_alembic_upgrade(self) -> None:
        """Bring the schema up to ``head`` via the packaged Alembic scripts.

        Runs :func:`alembic.command.upgrade` against the ``_alembic/``
        directory shipped alongside this module, using the current
        engine URL. Executed inside :meth:`asyncio.to_thread` because
        Alembic's runtime is synchronous (it drives an async engine
        internally via :func:`asyncio.run` inside its own ``env.py``,
        so calling it from the main event loop would nest loops and
        deadlock).

        Failures propagate: a broken migration MUST prevent
        ``__aenter__`` from returning a half-configured storage.
        """
        import asyncio
        from pathlib import Path

        from alembic import command
        from alembic.config import Config

        alembic_dir = Path(__file__).resolve().parent / "_alembic"
        cfg = Config(str(alembic_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(alembic_dir))
        cfg.set_main_option("sqlalchemy.url", self._url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

    async def aclose(self) -> None:
        """Dispose the engine (if owned) and drop the session factory."""
        if self._engine is not None and self._owns_engine:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None

    # ------------------------------------------------------------------
    # Small helpers shared by every method
    # ------------------------------------------------------------------

    def _session(self) -> "AsyncSession":
        """Return a fresh :class:`AsyncSession` from the factory.

        Raises:
            `RuntimeError`:
                If the storage has not been entered as an async
                context manager yet.
        """
        if self._session_factory is None:
            raise RuntimeError(
                "SqlStorage is not initialised — use `async with storage:`.",
            )
        return self._session_factory()

    async def _write_row(
        self,
        row_cls: type,
        record: Any,
        *,
        preserve_created_at: bool = True,
    ) -> Any:
        """Insert or update *record* via *row_cls*.

        Reads the existing row (if any) to preserve ``created_at``
        (mirrors the Redis backend's semantics — an upsert refreshes
        ``updated_at`` but keeps the original creation time), stamps
        ``updated_at`` to the current UTC time, and writes.  Returns
        the (possibly-updated) record so callers can propagate the
        stamped timestamps.

        Args:
            row_cls (`type`):
                Concrete ``*Row`` class from :mod:`_tables`.
            record (`Any`):
                The pydantic record to write.  Mutated in place —
                ``created_at`` / ``updated_at`` are refreshed.
            preserve_created_at (`bool`, defaults to `True`):
                When `False`, the caller's ``created_at`` is written
                verbatim.  Used by the very first insert paths where
                no prior row can exist.

        Returns:
            `Any`:
                The (mutated) record.
        """
        async with self._session() as sess:
            existing = None
            if preserve_created_at:
                existing = await sess.get(row_cls, record.id)
            record.updated_at = _utcnow()
            if existing is not None:
                # Preserve the original creation time on update.
                record.created_at = existing.created_at
                new_row = _from_record(row_cls, record)
                # Overwrite every mutable column in place so the SA
                # session tracks the update.
                for col in ("updated_at", "payload") + tuple(
                    row_cls.get_indexed_fields(),
                ):
                    setattr(existing, col, getattr(new_row, col))
            else:
                # First insert — anchor created_at to UTC too (the
                # record default uses machine-local ``datetime.now()``).
                record.created_at = _to_naive_utc(record.created_at)
                new_row = _from_record(row_cls, record)
                sess.add(new_row)
            await sess.commit()
        return record

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    async def _generate_credential_name(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Mirror :meth:`RedisStorage._generate_credential_name`.

        Auto-derive a unique per-user display name from the
        credential type (``"OpenAI"``, ``"OpenAI (2)"`` …) so the
        service layer does not have to.
        """
        cred_type = getattr(credential_data, "type", "")
        base_name = (
            cred_type.removesuffix("_credential").replace("_", " ").title()
        )
        if not base_name:
            base_name = "Credential"

        existing = await self.list_credentials(user_id)
        same_type_names = [
            c.data.get("name", "")
            for c in existing
            if c.data.get("type") == cred_type and c.id != credential_data.id
        ]

        if base_name not in same_type_names:
            return base_name

        idx = 2
        while f"{base_name} ({idx})" in same_type_names:
            idx += 1
        return f"{base_name} ({idx})"

    async def upsert_credential(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Create or update a credential record for *user_id*.

        Same contract as :meth:`RedisStorage.upsert_credential` — see
        that method for the id / name handling rules.
        """
        if not credential_data.name:
            credential_data.name = await self._generate_credential_name(
                user_id,
                credential_data,
            )

        data_dump = _dump_with_secrets(credential_data)

        if credential_data.id:
            async with self._session() as sess:
                existing = await sess.get(CredentialRow, credential_data.id)
            if existing is not None:
                record = _to_record(existing, CredentialRecord)
                record.data = data_dump
            else:
                record = CredentialRecord(
                    id=credential_data.id,
                    user_id=user_id,
                    data=data_dump,
                )
        else:
            record = CredentialRecord(
                user_id=user_id,
                data=data_dump,
            )

        await self._write_row(CredentialRow, record)
        return record.id

    async def list_credentials(self, user_id: str) -> list[CredentialRecord]:
        """Return every credential for *user_id*."""
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(CredentialRow).where(
                            CredentialRow.user_id == user_id,
                        ),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, CredentialRecord) for r in rows]

    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord | None:
        """Fetch one credential record; enforces owner-scoping."""
        async with self._session() as sess:
            row = await sess.get(CredentialRow, credential_id)
        if row is None or row.user_id != user_id:
            return None
        return _to_record(row, CredentialRecord)

    async def delete_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> bool:
        """Delete a credential; owner-scoped."""
        from sqlalchemy import delete

        async with self._session() as sess:
            result = await sess.execute(
                delete(CredentialRow).where(
                    CredentialRow.id == credential_id,
                    CredentialRow.user_id == user_id,
                ),
            )
            await sess.commit()
        return result.rowcount > 0

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    async def upsert_agent(
        self,
        user_id: str,
        agent_record: AgentRecord,
    ) -> str:
        """Persist an agent record (create or overwrite)."""
        _ = user_id  # scoping is enforced by the caller
        await self._write_row(AgentRow, agent_record)
        return agent_record.id

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """Return the user's ``source='user'`` agents only.

        Matches :meth:`RedisStorage.list_agents` — team-spawned
        workers (``source='team'``) are addressable by id but are
        never enumerated in the user's regular agent list.
        """
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(AgentRow).where(
                            AgentRow.user_id == user_id,
                            AgentRow.source == "user",
                        ),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, AgentRecord) for r in rows]

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Fetch one agent record; owner-scoped."""
        async with self._session() as sess:
            row = await sess.get(AgentRow, agent_id)
        if row is None or row.user_id != user_id:
            return None
        return _to_record(row, AgentRecord)

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete an agent + cascade sessions, schedules, team refs.

        Mirrors the cascade order of
        :meth:`RedisStorage.delete_agent`.  Runs the cascade in the
        Python layer (no ``ON DELETE CASCADE`` foreign keys) so
        semantics are identical across backends and tests can exercise
        the same code path.
        """
        # Cascade: sessions
        for session in await self.list_sessions(user_id, agent_id):
            await self.delete_session(user_id, agent_id, session.id)

        # Cascade: schedules owned by this agent
        for schedule in await self.list_schedules(user_id):
            if schedule.agent_id == agent_id:
                await self.delete_schedule(user_id, schedule.id)

        # Defensive: scrub the agent from every team roster (both the
        # legacy ``member_ids`` list and the modern ``members`` list).
        for team in await self.list_teams(user_id):
            dirty = False
            if agent_id in team.data.member_ids:
                team.data.member_ids = [
                    m for m in team.data.member_ids if m != agent_id
                ]
                dirty = True
            filtered = [m for m in team.data.members if m.agent_id != agent_id]
            if len(filtered) != len(team.data.members):
                team.data.members = filtered
                dirty = True
            if dirty:
                await self.upsert_team(user_id, team)

        from sqlalchemy import delete

        async with self._session() as sess:
            result = await sess.execute(
                delete(AgentRow).where(
                    AgentRow.id == agent_id,
                    AgentRow.user_id == user_id,
                ),
            )
            await sess.commit()
        return result.rowcount > 0

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        state: AgentState | None = None,
        session_id: str | None = None,
        source: SessionSource = SessionSource.USER,
        source_schedule_id: str | None = None,
    ) -> SessionRecord:
        """Create or update a session — same shape as the Redis backend."""
        if session_id:
            async with self._session() as sess:
                existing = await sess.get(SessionRow, session_id)
            if existing is not None:
                record = _to_record(existing, SessionRecord)
                record.config = config
                if state is not None:
                    record.state = state
                await self._write_row(SessionRow, record)
                return record

        new_id_kwargs = {"id": session_id} if session_id else {}
        record = SessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            config=config,
            source=source,
            source_schedule_id=source_schedule_id,
            state=state if state is not None else AgentState(),
            **new_id_kwargs,
        )
        await self._write_row(
            SessionRow,
            record,
            preserve_created_at=False,
        )
        return record

    async def set_session_team_id(
        self,
        user_id: str,
        session_id: str,
        team_id: str | None,
    ) -> None:
        """Single-column UPDATE — atomic on all supported dialects."""
        from sqlalchemy import update

        now = _utcnow()
        async with self._session() as sess:
            await sess.execute(
                update(SessionRow)
                .where(
                    SessionRow.id == session_id,
                    SessionRow.user_id == user_id,
                )
                .values(team_id=team_id, updated_at=now),
            )
            await sess.commit()

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Read-modify-write on the payload; raises if absent."""
        _ = user_id, agent_id  # scoping enforced by caller
        async with self._session() as sess:
            row = await sess.get(SessionRow, session_id)
            if row is None:
                raise KeyError(f"Session {session_id!r} not found.")
            record = _to_record(row, SessionRecord)
            record.state = state
            record.updated_at = _utcnow()
            new_row = _from_record(SessionRow, record)
            row.payload = new_row.payload
            row.updated_at = new_row.updated_at
            await sess.commit()

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Sessions for a (user, agent) pair — newest first."""
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(SessionRow)
                        .where(
                            SessionRow.user_id == user_id,
                            SessionRow.agent_id == agent_id,
                        )
                        .order_by(SessionRow.created_at.desc()),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, SessionRecord) for r in rows]

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """One session; owner-scoped."""
        _ = agent_id
        async with self._session() as sess:
            row = await sess.get(SessionRow, session_id)
        if row is None or row.user_id != user_id:
            return None
        return _to_record(row, SessionRecord)

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete a session + cascade (message log, leader-team)."""
        async with self._session() as sess:
            row = await sess.get(SessionRow, session_id)
        if row is None or row.user_id != user_id:
            return False
        record = _to_record(row, SessionRecord)

        # If this session leads a team, dissolve the team first.
        if record.team_id:
            team = await self.get_team(user_id, record.team_id)
            if team is not None and team.session_id == session_id:
                await self.delete_team(user_id, record.team_id)

        from sqlalchemy import delete

        async with self._session() as sess:
            await sess.execute(
                delete(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.user_id == user_id,
                ),
            )
            await sess.execute(
                delete(MessageRow).where(
                    MessageRow.session_id == session_id,
                ),
            )
            await sess.commit()
        _ = agent_id
        return True

    async def list_sessions_by_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> list[SessionRecord]:
        """Sessions created by *schedule_id* — newest first."""
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(SessionRow)
                        .where(
                            SessionRow.user_id == user_id,
                            SessionRow.source_schedule_id == schedule_id,
                        )
                        .order_by(SessionRow.created_at.desc()),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, SessionRecord) for r in rows]

    # ------------------------------------------------------------------
    # Schedules
    # ------------------------------------------------------------------

    async def upsert_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """Persist a schedule record (create or overwrite)."""
        _ = user_id
        await self._write_row(ScheduleRow, record)
        return record.id

    async def get_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> ScheduleRecord | None:
        """One schedule; owner-scoped."""
        async with self._session() as sess:
            row = await sess.get(ScheduleRow, schedule_id)
        if row is None or row.user_id != user_id:
            return None
        return _to_record(row, ScheduleRecord)

    async def list_schedules(
        self,
        user_id: str,
    ) -> list[ScheduleRecord]:
        """All schedules for a user."""
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(ScheduleRow).where(
                            ScheduleRow.user_id == user_id,
                        ),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, ScheduleRecord) for r in rows]

    async def delete_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> bool:
        """Delete a schedule + cascade its execution sessions."""
        record = await self.get_schedule(user_id, schedule_id)
        if record is None:
            return False

        for session in await self.list_sessions_by_schedule(
            user_id,
            schedule_id,
        ):
            await self.delete_session(user_id, record.agent_id, session.id)

        from sqlalchemy import delete

        async with self._session() as sess:
            await sess.execute(
                delete(ScheduleRow).where(
                    ScheduleRow.id == schedule_id,
                    ScheduleRow.user_id == user_id,
                ),
            )
            await sess.commit()
        return True

    async def list_all_schedules(self) -> list[ScheduleRecord]:
        """Every schedule across every user (used on startup)."""
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (await sess.execute(select(ScheduleRow))).scalars().all()
        return [_to_record(r, ScheduleRecord) for r in rows]

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        msg: Msg,
    ) -> None:
        """Insert-or-update by ``(session_id, msg_id)``.

        Semantics mirror :meth:`RedisStorage.upsert_message` closely
        enough for every caller in the codebase — the Redis version
        only replaces when the *tail* message matches; ours replaces
        on any matching id.  The looser rule is safe because callers
        never reuse a message id across turns.
        """
        _ = user_id  # scoping enforced by caller
        from sqlalchemy import update

        now = _utcnow()
        payload = msg.model_dump(mode="json")

        async with self._session() as sess:
            result = await sess.execute(
                update(MessageRow)
                .where(
                    MessageRow.session_id == session_id,
                    MessageRow.msg_id == msg.id,
                )
                .values(payload=payload),
            )
            if result.rowcount == 0:
                sess.add(
                    MessageRow(
                        session_id=session_id,
                        msg_id=msg.id,
                        payload=payload,
                        created_at=now,
                    ),
                )
            await sess.commit()

    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> Msg | None:
        """Fetch a message by ``(session_id, msg_id)``."""
        _ = user_id
        from sqlalchemy import select

        async with self._session() as sess:
            row = (
                await sess.execute(
                    select(MessageRow).where(
                        MessageRow.session_id == session_id,
                        MessageRow.msg_id == message_id,
                    ),
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return Msg.model_validate(row.payload)

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Msg]:
        """Paginated message list, chronological order."""
        _ = user_id
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(MessageRow)
                        .where(MessageRow.session_id == session_id)
                        .order_by(
                            MessageRow.created_at.asc(),
                            MessageRow.msg_id.asc(),
                        )
                        .offset(offset)
                        .limit(limit),
                    )
                )
                .scalars()
                .all()
            )
        return [Msg.model_validate(r.payload) for r in rows]

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    async def upsert_team(
        self,
        user_id: str,
        record: TeamRecord,
    ) -> TeamRecord:
        """Persist a team record."""
        _ = user_id
        await self._write_row(TeamRow, record)
        return record

    async def get_team(
        self,
        user_id: str,
        team_id: str,
    ) -> TeamRecord | None:
        """One team; owner-scoped."""
        async with self._session() as sess:
            row = await sess.get(TeamRow, team_id)
        if row is None or row.user_id != user_id:
            return None
        return _to_record(row, TeamRecord)

    async def list_teams(self, user_id: str) -> list[TeamRecord]:
        """All teams for a user."""
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(TeamRow).where(TeamRow.user_id == user_id),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, TeamRecord) for r in rows]

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Delete a team + role-aware member cleanup."""
        # Local import mirrors :meth:`RedisStorage.delete_team` — the
        # helper walks storage recursively so we resolve it lazily to
        # avoid a top-level import cycle.
        from .._utils import _ensure_team_members

        team = await self.get_team(user_id, team_id)
        if team is None:
            return False

        for member in await _ensure_team_members(self, user_id, team):
            if member.role == "created":
                await self.delete_agent(member.owner_id, member.agent_id)
            else:  # invited
                await self.delete_session(
                    member.owner_id,
                    member.agent_id,
                    member.session_id,
                )

        # Detach the leader session (idempotent).
        await self.set_session_team_id(user_id, team.session_id, None)

        from sqlalchemy import delete

        async with self._session() as sess:
            await sess.execute(
                delete(TeamRow).where(
                    TeamRow.id == team_id,
                    TeamRow.user_id == user_id,
                ),
            )
            await sess.commit()
        return True

    # ------------------------------------------------------------------
    # Knowledge bases
    # ------------------------------------------------------------------

    async def upsert_knowledge_base(
        self,
        user_id: str,
        record: KnowledgeBaseRecord,
    ) -> KnowledgeBaseRecord:
        """Create or update a KB record; enforces ``record.user_id``."""
        if record.user_id != user_id:
            raise ValueError(
                "record.user_id does not match the given user_id.",
            )
        await self._write_row(KnowledgeBaseRow, record)
        return record

    async def get_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> KnowledgeBaseRecord | None:
        """One KB; owner-scoped."""
        async with self._session() as sess:
            row = await sess.get(KnowledgeBaseRow, knowledge_base_id)
        if row is None or row.user_id != user_id:
            return None
        return _to_record(row, KnowledgeBaseRecord)

    async def list_knowledge_bases(
        self,
        user_id: str,
    ) -> list[KnowledgeBaseRecord]:
        """All KBs for a user."""
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(KnowledgeBaseRow).where(
                            KnowledgeBaseRow.user_id == user_id,
                        ),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, KnowledgeBaseRecord) for r in rows]

    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> bool:
        """Delete a KB + cascade its document rows."""
        from sqlalchemy import delete

        async with self._session() as sess:
            # Cascade document rows for this KB in one shot.
            await sess.execute(
                delete(KnowledgeDocumentRow).where(
                    KnowledgeDocumentRow.user_id == user_id,
                    KnowledgeDocumentRow.knowledge_base_id
                    == knowledge_base_id,
                ),
            )
            result = await sess.execute(
                delete(KnowledgeBaseRow).where(
                    KnowledgeBaseRow.id == knowledge_base_id,
                    KnowledgeBaseRow.user_id == user_id,
                ),
            )
            await sess.commit()
        return result.rowcount > 0

    # ------------------------------------------------------------------
    # Knowledge documents
    # ------------------------------------------------------------------

    async def upsert_knowledge_document(
        self,
        user_id: str,
        record: KnowledgeDocumentRecord,
    ) -> KnowledgeDocumentRecord:
        """Create or update a document record; enforces ``record.user_id``."""
        if record.user_id != user_id:
            raise ValueError(
                "record.user_id does not match the given user_id.",
            )
        await self._write_row(KnowledgeDocumentRow, record)
        return record

    async def get_knowledge_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeDocumentRecord | None:
        """One document; owner + KB scoped."""
        async with self._session() as sess:
            row = await sess.get(KnowledgeDocumentRow, document_id)
        if (
            row is None
            or row.user_id != user_id
            or row.knowledge_base_id != knowledge_base_id
        ):
            return None
        return _to_record(row, KnowledgeDocumentRecord)

    async def list_knowledge_documents(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> list[KnowledgeDocumentRecord]:
        """All documents inside a KB."""
        from sqlalchemy import select

        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(KnowledgeDocumentRow).where(
                            KnowledgeDocumentRow.user_id == user_id,
                            KnowledgeDocumentRow.knowledge_base_id
                            == knowledge_base_id,
                        ),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, KnowledgeDocumentRecord) for r in rows]

    async def delete_knowledge_document(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> bool:
        """Delete one document."""
        from sqlalchemy import delete

        async with self._session() as sess:
            result = await sess.execute(
                delete(KnowledgeDocumentRow).where(
                    KnowledgeDocumentRow.id == document_id,
                    KnowledgeDocumentRow.user_id == user_id,
                    KnowledgeDocumentRow.knowledge_base_id
                    == knowledge_base_id,
                ),
            )
            await sess.commit()
        return result.rowcount > 0

    async def update_knowledge_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        status: KnowledgeDocumentStatus,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Fast-path column UPDATE + optional payload rewrite.

        ``status`` is a column so it can always be updated with a
        single ``UPDATE``; ``error`` / ``chunk_count`` live inside
        :attr:`~KnowledgeDocumentRecord.data` (payload) and therefore
        require the classic read-modify-write when they change.
        """
        async with self._session() as sess:
            row = await sess.get(KnowledgeDocumentRow, document_id)
            if (
                row is None
                or row.user_id != user_id
                or row.knowledge_base_id != knowledge_base_id
            ):
                return
            record = _to_record(row, KnowledgeDocumentRecord)
            record.status = status
            if error is not None:
                record.data.error = error
            if chunk_count is not None:
                record.data.chunk_count = chunk_count
            record.updated_at = _utcnow()
            new_row = _from_record(KnowledgeDocumentRow, record)
            row.status = new_row.status
            row.payload = new_row.payload
            row.updated_at = new_row.updated_at
            await sess.commit()

    async def acquire_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
        lease_ttl: timedelta,
        now: datetime | None = None,
    ) -> bool:
        """Conditional UPDATE — atomic on every supported dialect."""
        from sqlalchemy import or_, update

        now = _to_naive_utc(now) if now is not None else _utcnow()
        deadline = now + lease_ttl

        async with self._session() as sess:
            result = await sess.execute(
                update(KnowledgeDocumentRow)
                .where(
                    KnowledgeDocumentRow.id == document_id,
                    KnowledgeDocumentRow.user_id == user_id,
                    KnowledgeDocumentRow.knowledge_base_id
                    == knowledge_base_id,
                    or_(
                        KnowledgeDocumentRow.processing_node.is_(None),
                        KnowledgeDocumentRow.lease_expires_at.is_(None),
                        KnowledgeDocumentRow.lease_expires_at < now,
                    ),
                )
                .values(
                    processing_node=processing_node,
                    lease_expires_at=deadline,
                    updated_at=now,
                ),
            )
            await sess.commit()
        return result.rowcount > 0

    async def renew_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
        lease_ttl: timedelta,
        now: datetime | None = None,
    ) -> bool:
        """Conditional UPDATE constrained to the current holder."""
        from sqlalchemy import update

        now = _to_naive_utc(now) if now is not None else _utcnow()
        deadline = now + lease_ttl

        async with self._session() as sess:
            result = await sess.execute(
                update(KnowledgeDocumentRow)
                .where(
                    KnowledgeDocumentRow.id == document_id,
                    KnowledgeDocumentRow.user_id == user_id,
                    KnowledgeDocumentRow.knowledge_base_id
                    == knowledge_base_id,
                    KnowledgeDocumentRow.processing_node == processing_node,
                )
                .values(lease_expires_at=deadline, updated_at=now),
            )
            await sess.commit()
        return result.rowcount > 0

    async def release_knowledge_document_lease(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        processing_node: str,
    ) -> None:
        """Conditional UPDATE constrained to the current holder."""
        from sqlalchemy import update

        now = _utcnow()
        async with self._session() as sess:
            await sess.execute(
                update(KnowledgeDocumentRow)
                .where(
                    KnowledgeDocumentRow.id == document_id,
                    KnowledgeDocumentRow.user_id == user_id,
                    KnowledgeDocumentRow.knowledge_base_id
                    == knowledge_base_id,
                    KnowledgeDocumentRow.processing_node == processing_node,
                )
                .values(
                    processing_node=None,
                    lease_expires_at=None,
                    updated_at=now,
                ),
            )
            await sess.commit()

    async def list_knowledge_documents_with_expired_lease(
        self,
        now: datetime | None = None,
    ) -> list[KnowledgeDocumentRecord]:
        """Documents past their lease deadline, non-terminal, held."""
        from sqlalchemy import select

        now = _to_naive_utc(now) if now is not None else _utcnow()
        terminal = ("ready", "error")
        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(KnowledgeDocumentRow).where(
                            KnowledgeDocumentRow.status.notin_(terminal),
                            KnowledgeDocumentRow.processing_node.is_not(None),
                            KnowledgeDocumentRow.lease_expires_at.is_not(None),
                            KnowledgeDocumentRow.lease_expires_at < now,
                        ),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, KnowledgeDocumentRecord) for r in rows]

    async def list_knowledge_documents_pending_since(
        self,
        threshold: datetime,
    ) -> list[KnowledgeDocumentRecord]:
        """Documents stuck in ``pending`` before *threshold*."""
        from sqlalchemy import select

        threshold = _to_naive_utc(threshold)
        async with self._session() as sess:
            rows = (
                (
                    await sess.execute(
                        select(KnowledgeDocumentRow).where(
                            KnowledgeDocumentRow.status == "pending",
                            KnowledgeDocumentRow.created_at < threshold,
                        ),
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r, KnowledgeDocumentRecord) for r in rows]
