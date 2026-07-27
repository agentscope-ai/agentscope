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


class AsyncSQLAlchemyStorage(StorageBase):
    """Async SQLAlchemy-backed :class:`StorageBase` implementation.

    Instantiate with any SQLAlchemy async URL (the concrete driver is
    a caller-installed dependency, not part of the ``sql`` extra):

    .. code-block:: python

        storage = AsyncSQLAlchemyStorage("sqlite+aiosqlite:///./as.db")
        async with storage:
            ...

    ``__aenter__`` builds the engine and (optionally) provisions the
    schema; ``aclose`` disposes the engine.
    """

    def __init__(
        self,
        url: str,
        *,
        create_tables: bool = True,
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
            create_tables (`bool`, defaults to `True`):
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
            # SQLite ignores foreign keys unless enabled per connection,
            # so ``ON DELETE CASCADE`` would silently not fire. Turn it
            # on for every pooled connection.
            if self._engine.sync_engine.dialect.name == "sqlite":
                from sqlalchemy import event

                @event.listens_for(self._engine.sync_engine, "connect")
                def _enable_sqlite_fk(dbapi_conn: Any, _rec: Any) -> None:
                    cursor = dbapi_conn.cursor()
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.close()

        # ``expire_on_commit=False`` keeps mapper-returned rows readable
        # after commit — :class:`AsyncSQLAlchemyStorage` immediately
        # projects rows back into pydantic records outside the session
        # scope, which would otherwise trigger a detached-load error.
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
                "AsyncSQLAlchemyStorage is not initialised — use "
                "`async with storage:`.",
            )
        return self._session_factory()

    def _upsert_stmt(
        self,
        row_cls: type,
        values: dict[str, Any],
        conflict_cols: list[str],
        update_cols: tuple[str, ...],
    ) -> Any:
        """Build a dialect-native atomic upsert statement.

        A single ``INSERT ... ON CONFLICT DO UPDATE`` (Postgres /
        SQLite) or ``INSERT ... ON DUPLICATE KEY UPDATE`` (MySQL /
        MariaDB) makes the insert-or-update decision **inside the
        database**, so concurrent writers on the same key can no longer
        race between a separate read and write (the failure mode of the
        classic read-then-write upsert).  ``conflict_cols`` name the
        key that triggers the "update instead" branch; ``update_cols``
        are the columns overwritten on conflict — deliberately
        excluding ``id`` / ``created_at`` so an upsert refreshes the
        mutable columns while keeping the original creation time.

        Args:
            row_cls (`type`):
                The concrete ``*Row`` class whose table is targeted.
            values (`dict[str, Any]`):
                Full column → value mapping for the INSERT branch.
            conflict_cols (`list[str]`):
                Column names forming the conflict target (the primary
                key, or the composite key for messages).
            update_cols (`tuple[str, ...]`):
                Column names to overwrite on conflict.

        Returns:
            `Any`:
                A dialect-specific insert construct ready to execute.

        Raises:
            `NotImplementedError`:
                For dialects without a supported native upsert
                (e.g. Oracle / SQL Server, which would need ``MERGE``).
        """
        assert self._engine is not None
        dialect = self._engine.sync_engine.dialect.name
        table = row_cls.__table__

        if dialect in ("postgresql", "sqlite"):
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as _insert
            else:
                from sqlalchemy.dialects.sqlite import insert as _insert
            stmt = _insert(table).values(**values)
            return stmt.on_conflict_do_update(
                index_elements=conflict_cols,
                set_={c: getattr(stmt.excluded, c) for c in update_cols},
            )

        if dialect in ("mysql", "mariadb"):
            from sqlalchemy.dialects.mysql import insert as _insert

            stmt = _insert(table).values(**values)
            return stmt.on_duplicate_key_update(
                **{c: getattr(stmt.inserted, c) for c in update_cols},
            )

        raise NotImplementedError(
            f"Atomic upsert is unsupported for the {dialect!r} dialect; "
            "supported dialects are postgresql, sqlite, mysql, mariadb.",
        )

    async def _write_row(
        self,
        row_cls: type,
        record: Any,
        *,
        preserve_created_at: bool = True,
    ) -> Any:
        """Atomically insert-or-update *record* via *row_cls*.

        Issues a single dialect-native upsert (see :meth:`_upsert_stmt`)
        so the insert-or-update decision is made inside the database —
        an upsert refreshes ``updated_at`` / ``payload`` / the indexed
        columns but keeps the original ``created_at`` (mirroring the
        Redis backend's semantics).  Because the statement is atomic
        there is no read-then-write window for concurrent writers on
        the same id to race through.  Returns the (mutated) record so
        callers can propagate the stamped timestamps.

        Args:
            row_cls (`type`):
                Concrete ``*Row`` class from :mod:`_tables`.
            record (`Any`):
                The pydantic record to write.  Mutated in place —
                ``created_at`` / ``updated_at`` are refreshed.
            preserve_created_at (`bool`, defaults to `True`):
                When `True`, the DB-authoritative ``created_at`` (the
                original one on an update) is read back into the
                returned record.  Set `False` on pure-create paths
                where no prior row can exist, to skip that read.

        Returns:
            `Any`:
                The (mutated) record.
        """
        from sqlalchemy import select

        record.updated_at = _utcnow()
        # The record default uses machine-local ``datetime.now()``;
        # anchor it to UTC for the INSERT branch.  On conflict the DB
        # keeps its stored ``created_at`` and we read it back below.
        record.created_at = _to_naive_utc(record.created_at)
        new_row = _from_record(row_cls, record)
        indexed = tuple(row_cls.get_indexed_fields())
        values = {
            col: getattr(new_row, col)
            for col in ("id", "created_at", "updated_at", "payload") + indexed
        }
        update_cols = ("updated_at", "payload") + indexed

        async with self._session() as sess:
            await sess.execute(
                self._upsert_stmt(row_cls, values, ["id"], update_cols),
            )
            if preserve_created_at:
                # Reflect the creation time the DB kept on conflict
                # back into the returned record.
                record.created_at = (
                    await sess.execute(
                        select(row_cls.created_at).where(
                            row_cls.id == record.id,
                        ),
                    )
                ).scalar_one()
            await sess.commit()
        return record

    # ------------------------------------------------------------------
    # Cascade-delete internals — every ``_delete_*_impl`` runs on the
    # caller-supplied ``sess`` WITHOUT committing, so the public
    # ``delete_*`` wrapper can run the whole (mutually recursive)
    # cascade inside a single transaction and commit once. That makes
    # the compound deletes atomic: a mid-cascade failure rolls the
    # whole thing back instead of leaving a half-deleted graph.
    #
    # Reads use ``select`` (not ``session.get``) so they see the
    # transaction's own uncommitted deletes instead of stale
    # identity-map rows.
    # ------------------------------------------------------------------

    async def _delete_session_impl(
        self,
        sess: "AsyncSession",
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete a session (+ its messages, + leader-team) on *sess*."""
        from sqlalchemy import delete, select

        _ = agent_id  # scoping enforced by caller
        row = (
            await sess.execute(
                select(SessionRow).where(SessionRow.id == session_id),
            )
        ).scalar_one_or_none()
        if row is None or row.user_id != user_id:
            return False
        record = _to_record(row, SessionRecord)

        # If this session leads a team, dissolve the team first.
        if record.team_id:
            team_row = (
                await sess.execute(
                    select(TeamRow).where(TeamRow.id == record.team_id),
                )
            ).scalar_one_or_none()
            if (
                team_row is not None
                and _to_record(team_row, TeamRecord).session_id == session_id
            ):
                await self._delete_team_impl(sess, user_id, record.team_id)

        await sess.execute(
            delete(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.user_id == user_id,
            ),
        )
        await sess.execute(
            delete(MessageRow).where(MessageRow.session_id == session_id),
        )
        return True

    async def _delete_schedule_impl(
        self,
        sess: "AsyncSession",
        user_id: str,
        schedule_id: str,
    ) -> bool:
        """Delete a schedule + cascade its execution sessions on *sess*."""
        from sqlalchemy import delete, select

        row = (
            await sess.execute(
                select(ScheduleRow).where(ScheduleRow.id == schedule_id),
            )
        ).scalar_one_or_none()
        if row is None or row.user_id != user_id:
            return False

        sessions = (
            (
                await sess.execute(
                    select(SessionRow).where(
                        SessionRow.user_id == user_id,
                        SessionRow.source_schedule_id == schedule_id,
                    ),
                )
            )
            .scalars()
            .all()
        )
        for s in sessions:
            await self._delete_session_impl(sess, user_id, s.agent_id, s.id)

        await sess.execute(
            delete(ScheduleRow).where(
                ScheduleRow.id == schedule_id,
                ScheduleRow.user_id == user_id,
            ),
        )
        return True

    async def _delete_agent_impl(
        self,
        sess: "AsyncSession",
        user_id: str,
        agent_id: str,
    ) -> bool:
        """Delete an agent + cascade sessions/schedules/team refs on *sess*."""
        from sqlalchemy import delete, select, update

        # Cascade: sessions owned by this agent.
        sessions = (
            (
                await sess.execute(
                    select(SessionRow).where(
                        SessionRow.user_id == user_id,
                        SessionRow.agent_id == agent_id,
                    ),
                )
            )
            .scalars()
            .all()
        )
        for s in sessions:
            await self._delete_session_impl(sess, user_id, agent_id, s.id)

        # Cascade: schedules owned by this agent.
        schedules = (
            (
                await sess.execute(
                    select(ScheduleRow).where(
                        ScheduleRow.user_id == user_id,
                        ScheduleRow.agent_id == agent_id,
                    ),
                )
            )
            .scalars()
            .all()
        )
        for sch in schedules:
            await self._delete_schedule_impl(sess, user_id, sch.id)

        # Defensive: scrub the agent from every surviving team roster
        # (both the legacy ``member_ids`` list and the ``members`` list).
        teams = (
            (
                await sess.execute(
                    select(TeamRow).where(TeamRow.user_id == user_id),
                )
            )
            .scalars()
            .all()
        )
        for team_row in teams:
            team_rec = _to_record(team_row, TeamRecord)
            dirty = False
            if agent_id in team_rec.data.member_ids:
                team_rec.data.member_ids = [
                    m for m in team_rec.data.member_ids if m != agent_id
                ]
                dirty = True
            filtered = [
                m for m in team_rec.data.members if m.agent_id != agent_id
            ]
            if len(filtered) != len(team_rec.data.members):
                team_rec.data.members = filtered
                dirty = True
            if dirty:
                team_rec.updated_at = _utcnow()
                new_row = _from_record(TeamRow, team_rec)
                await sess.execute(
                    update(TeamRow)
                    .where(TeamRow.id == team_rec.id)
                    .values(
                        payload=new_row.payload,
                        updated_at=new_row.updated_at,
                    ),
                )

        result = await sess.execute(
            delete(AgentRow).where(
                AgentRow.id == agent_id,
                AgentRow.user_id == user_id,
            ),
        )
        return result.rowcount > 0

    async def _delete_team_impl(
        self,
        sess: "AsyncSession",
        user_id: str,
        team_id: str,
    ) -> bool:
        """Role-aware team cleanup + leader-detach + delete, on *sess*."""
        from sqlalchemy import delete, select, update

        from .._model._team import TeamMember

        row = (
            await sess.execute(
                select(TeamRow).where(TeamRow.id == team_id),
            )
        ).scalar_one_or_none()
        if row is None or row.user_id != user_id:
            return False
        team = _to_record(row, TeamRecord)

        # Materialise the roster. Modern records carry ``members``;
        # legacy ``member_ids``-only records are migrated inline on
        # *sess* (no writeback — the team is being deleted anyway).
        members = list(team.data.members)
        if not members and team.data.member_ids:
            for aid in team.data.member_ids:
                worker = (
                    (
                        await sess.execute(
                            select(SessionRow).where(
                                SessionRow.user_id == user_id,
                                SessionRow.agent_id == aid,
                            ),
                        )
                    )
                    .scalars()
                    .first()
                )
                if worker is not None:
                    members.append(
                        TeamMember(
                            owner_id=user_id,
                            agent_id=aid,
                            session_id=worker.id,
                            role="created",
                        ),
                    )

        for member in members:
            if member.role == "created":
                await self._delete_agent_impl(
                    sess,
                    member.owner_id,
                    member.agent_id,
                )
            else:  # invited — only the borrowed session goes
                await self._delete_session_impl(
                    sess,
                    member.owner_id,
                    member.agent_id,
                    member.session_id,
                )

        # Detach the leader session (idempotent — it may already be gone).
        await sess.execute(
            update(SessionRow)
            .where(
                SessionRow.id == team.session_id,
                SessionRow.user_id == user_id,
            )
            .values(team_id=None, updated_at=_utcnow()),
        )
        await sess.execute(
            delete(TeamRow).where(
                TeamRow.id == team_id,
                TeamRow.user_id == user_id,
            ),
        )
        return True

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

        # Scope the create-or-update to *user_id*. The Redis backend
        # namespaces its keys by user, so a preset id can only ever
        # address the caller's own record; the SQL table keys on a
        # global primary id, so an unscoped lookup — or the id-keyed
        # upsert in ``_write_row`` — would let a caller read or clobber
        # another tenant's credential. Only a row the caller already
        # owns may be updated in place; any other preset id is created
        # fresh under the caller via a plain INSERT that fails loudly on
        # a global collision instead of overwriting the holder.
        if credential_data.id:
            from sqlalchemy import select

            async with self._session() as sess:
                existing = (
                    await sess.execute(
                        select(CredentialRow).where(
                            CredentialRow.id == credential_data.id,
                            CredentialRow.user_id == user_id,
                        ),
                    )
                ).scalar_one_or_none()
            if existing is not None:
                # The caller's own credential: the id-keyed upsert can
                # only touch this row, so update it in place.
                record = _to_record(existing, CredentialRecord)
                record.data = data_dump
                await self._write_row(CredentialRow, record)
                return record.id
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

        # A fresh record — either a server-generated id (never collides)
        # or a caller-preset id proven not to belong to the caller. Use
        # a plain INSERT rather than the id-conflict upsert so that a
        # preset id already held by another tenant raises IntegrityError
        # instead of silently overwriting them and stealing ownership.
        record.created_at = _to_naive_utc(record.created_at)
        record.updated_at = _utcnow()
        async with self._session() as sess:
            sess.add(_from_record(CredentialRow, record))
            await sess.commit()
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

        Mirrors the cascade order of :meth:`RedisStorage.delete_agent`.
        The whole cascade runs in a **single transaction** (see
        :meth:`_delete_agent_impl`) so it is atomic — a mid-cascade
        failure rolls back rather than leaving orphans.
        """
        async with self._session() as sess:
            ok = await self._delete_agent_impl(sess, user_id, agent_id)
            await sess.commit()
        return ok

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
        """Delete a session + cascade (message log, leader-team).

        Atomic: the session row, its message log, and any team it leads
        are removed inside one transaction (see
        :meth:`_delete_session_impl`).
        """
        async with self._session() as sess:
            ok = await self._delete_session_impl(
                sess,
                user_id,
                agent_id,
                session_id,
            )
            await sess.commit()
        return ok

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
        """Delete a schedule + cascade its execution sessions.

        Atomic: the schedule and every session it spawned are removed
        in one transaction (see :meth:`_delete_schedule_impl`).
        """
        async with self._session() as sess:
            ok = await self._delete_schedule_impl(sess, user_id, schedule_id)
            await sess.commit()
        return ok

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
        now = _utcnow()
        payload = msg.model_dump(mode="json")

        # Atomic upsert on the composite key: insert a new event, or
        # replace the payload of an existing ``(session_id, msg_id)``
        # while keeping its original ``created_at``.
        values = {
            "session_id": session_id,
            "msg_id": msg.id,
            "created_at": now,
            "payload": payload,
        }
        async with self._session() as sess:
            await sess.execute(
                self._upsert_stmt(
                    MessageRow,
                    values,
                    ["session_id", "msg_id"],
                    ("payload",),
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
        limit: int = 50,
        before: str | None = None,
        **kwargs: Any,
    ) -> tuple[list[Msg], bool]:
        """Most-recent page of messages with cursor-based pagination.

        Mirrors :meth:`RedisStorage.list_messages`: returns up to
        *limit* messages in chronological order together with a
        ``has_more`` flag that is ``True`` when older messages exist
        before the returned page. ``before`` is a message-id cursor —
        when set, the page ends just before that message; when ``None``
        the latest page is returned. An unknown ``before`` cursor
        yields ``([], False)``. The legacy ``offset`` keyword is
        accepted only to emit a ``DeprecationWarning`` and is ignored.
        """
        _ = user_id  # scoping enforced by caller
        if "offset" in kwargs:
            import warnings

            warnings.warn(
                "The 'offset' parameter is deprecated and will be "
                "removed in a future version. Use 'before' for "
                "cursor-based pagination instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        from sqlalchemy import and_, or_, select

        async with self._session() as sess:
            cond = MessageRow.session_id == session_id
            if before is not None:
                cursor = (
                    await sess.execute(
                        select(
                            MessageRow.created_at,
                            MessageRow.msg_id,
                        ).where(
                            MessageRow.session_id == session_id,
                            MessageRow.msg_id == before,
                        ),
                    )
                ).one_or_none()
                if cursor is None:
                    return [], False
                c_created, c_msg = cursor
                # Strictly older than the cursor in the
                # ``(created_at, msg_id)`` total order (spelled out
                # rather than a row-value comparison so every dialect
                # can plan it).
                cond = and_(
                    cond,
                    or_(
                        MessageRow.created_at < c_created,
                        and_(
                            MessageRow.created_at == c_created,
                            MessageRow.msg_id < c_msg,
                        ),
                    ),
                )

            # Fetch newest-first with one extra row to detect whether
            # older messages remain, then flip back to chronological.
            rows = list(
                (
                    await sess.execute(
                        select(MessageRow)
                        .where(cond)
                        .order_by(
                            MessageRow.created_at.desc(),
                            MessageRow.msg_id.desc(),
                        )
                        .limit(limit + 1),
                    )
                )
                .scalars()
                .all(),
            )

        has_more = len(rows) > limit
        page = rows[:limit]
        page.reverse()
        return [Msg.model_validate(r.payload) for r in page], has_more

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
        """Delete a team + role-aware member cleanup.

        Atomic: the role-aware member teardown (``created`` members
        deleted whole, ``invited`` members losing only their borrowed
        session), the leader detach, and the team-record delete all run
        in one transaction (see :meth:`_delete_team_impl`).
        """
        async with self._session() as sess:
            ok = await self._delete_team_impl(sess, user_id, team_id)
            await sess.commit()
        return ok

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
        """Delete a KB; its document rows cascade natively via the
        ``knowledge_documents.knowledge_base_id`` foreign key
        (``ON DELETE CASCADE``) — one atomic statement, no app loop.
        """
        from sqlalchemy import delete

        async with self._session() as sess:
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
