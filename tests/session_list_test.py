# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Session list projection, pagination and status compatibility tests."""
import asyncio
import json
from datetime import datetime
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.engine import URL

from agentscope.app._router._session import session_router
from agentscope.app._service import SessionService
from agentscope.app.deps import (
    get_current_user_id,
    get_message_bus,
    get_resource_access_service,
    get_storage,
)
from agentscope.app.storage import (
    AsyncSQLAlchemyStorage,
    ChannelOrigin,
    SessionConfig,
    SessionListItem,
    SessionRecord,
    StorageBase,
)
from agentscope.app.storage._sql import _storage
from agentscope.app.storage._sql._mappers import _from_record
from agentscope.app.storage._sql._session_list import session_list_columns
from agentscope.app.storage._sql._tables import SessionRow
from agentscope.message import AssistantMsg, ToolCallBlock, UserMsg
from agentscope.state import AgentState, ToolContext
from agentscope.state._state import ReadCacheEntry


class SessionListTest(IsolatedAsyncioTestCase):
    """Exercise the real SQL read path, plus the legacy storage fallback."""

    async def asyncSetUp(self) -> None:
        """Create a database with large records and every parked status."""
        self.storage = await self.enterAsyncContext(
            AsyncSQLAlchemyStorage("sqlite+aiosqlite:///:memory:"),
        )
        large = "long conversation or file content " * 40000
        contexts = [
            [],
            [AssistantMsg(name="a", content=large)],
            [self._assistant("submitted")],
            [self._assistant("submitted", "asking")],
            [self._assistant("finished")],
            [self._assistant("asking"), UserMsg(name="u", content=large)],
        ]
        self.statuses = [
            "idle",
            "idle",
            "awaiting_external_result",
            "awaiting_permission",
            "idle",
            "idle",
        ]
        now = datetime(2026, 1, 1)
        self.records = [
            SessionRecord(
                id=f"session-{index}",
                user_id="user",
                agent_id="agent",
                created_at=now,
                updated_at=now,
                config=SessionConfig(
                    name=f"Session {index}",
                    workspace_id="workspace",
                ),
                state=AgentState(
                    context=context,
                    summary=large,
                    tool_context=ToolContext(
                        read_file_cache=[
                            ReadCacheEntry(
                                file_path="cached.txt",
                                updated_at=0,
                                bytes=len(large),
                                lines=[large],
                            ),
                        ],
                    ),
                ),
            )
            for index, context in enumerate(contexts)
        ]
        async with self.storage._session() as sess:
            sess.add_all(
                [_from_record(SessionRow, record) for record in self.records],
            )
            for user, agent in (("other", "agent"), ("user", "other")):
                record = self.records[0].model_copy(
                    update={
                        "id": f"{user}-{agent}",
                        "user_id": user,
                        "agent_id": agent,
                    },
                )
                sess.add(_from_record(SessionRow, record))
            await sess.commit()

    @staticmethod
    def _assistant(*states: str) -> AssistantMsg:
        """Include large tool inputs to catch accidental tail loading."""
        return AssistantMsg(
            name="a",
            content=[
                ToolCallBlock(
                    id=f"call-{index}",
                    name="tool",
                    input=json.dumps({"large": "x" * 1000000}),
                    state=state,
                )
                for index, state in enumerate(states)
            ],
        )

    def _expected_record(self, record: SessionRecord) -> dict:
        """Preserve the complete response except the established trim."""
        result = record.model_dump(mode="json")
        result["state"].update(
            context=[],
            summary="",
            tool_context=ToolContext().model_dump(mode="json"),
        )
        return result

    async def test_pages_preserve_records_and_status(self) -> None:
        """SQL and inherited fallback agree on all fields and tail status."""
        for method in (
            self.storage.list_sessions_page,
            lambda *args, **kwargs: StorageBase.list_sessions_page(
                self.storage,
                *args,
                **kwargs,
            ),
        ):
            with self.subTest(method=method):
                actual = []
                for offset in (0, 2, 4, 6):
                    items, total = await method(
                        "user",
                        "agent",
                        limit=2,
                        offset=offset,
                    )
                    self.assertEqual(total, 6)
                    actual.extend(
                        (
                            item.session.model_dump(mode="json"),
                            SessionService.derive_tool_call_status(
                                item.tool_call_states,
                            ),
                        )
                        for item in items
                    )
                self.assertEqual(
                    actual,
                    [
                        (self._expected_record(record), status)
                        for record, status in reversed(
                            list(zip(self.records, self.statuses)),
                        )
                    ],
                )
                self.assertEqual(
                    await method("missing", "agent", limit=2),
                    ([], 0),
                )
                for options in ({"limit": 0}, {"offset": -1}):
                    with self.assertRaises(ValueError):
                        await method("user", "agent", **options)
        full = await self.storage.get_session("user", "agent", "session-3")
        self.assertEqual(
            full.model_dump(mode="json"),
            self.records[3].model_dump(mode="json"),
        )

    async def test_projection_is_small_before_deserialization(self) -> None:
        """The DB result itself excludes MB-sized text and tool inputs."""
        async with self.storage._session() as sess:
            result = await sess.execute(
                sa.select(*session_list_columns("sqlite")).where(
                    SessionRow.id == "session-3",
                ),
            )
            row = result.mappings().one()
        expected = self._expected_record(self.records[3])["state"]
        for field in ("context", "summary", "tool_context"):
            expected.pop(field)
        self.assertEqual(
            dict(row),
            {
                "payload": {
                    "origin": {"type": "user"},
                    "config": self.records[3].config.model_dump(mode="json"),
                    "state": expected,
                },
                "asking": True,
                "submitted": True,
            },
        )
        self.assertLess(len(json.dumps(dict(row))), 20000)

    async def test_legacy_full_list_sorts_only_ids_and_batches_reads(
        self,
    ) -> None:
        """Never combine payload expressions and ORDER BY, even unpaged."""
        records = [
            self.records[0].model_copy(
                update={
                    "id": f"batch-{index:03d}",
                    "user_id": "batch-user",
                    "state": AgentState(),
                },
            )
            for index in range(205)
        ]
        async with self.storage._session() as sess:
            sess.add_all(
                [_from_record(SessionRow, record) for record in records],
            )
            await sess.commit()
        queries = []

        def capture(
            _conn: object,
            _cursor: object,
            statement: str,
            parameters: tuple,
            _context: object,
            _many: bool,
        ) -> None:
            queries.append((statement, parameters))

        engine = self.storage._engine.sync_engine
        sa.event.listen(engine, "before_cursor_execute", capture)
        try:
            items, total = await self.storage.list_sessions_page(
                "batch-user",
                "agent",
            )
        finally:
            sa.event.remove(engine, "before_cursor_execute", capture)
        self.assertEqual(
            (total, [item.session.id for item in items]),
            (205, [record.id for record in reversed(records)]),
        )
        self.assertEqual(len(queries), 3)
        self.assertIn("ORDER BY", queries[0][0])
        self.assertNotIn("payload", queries[0][0])
        for statement, parameters in queries[1:]:
            self.assertIn("payload", statement)
            self.assertNotIn("ORDER BY", statement)
            self.assertLess(len(parameters), 250)

    async def test_full_list_total_after_concurrent_deletion(self) -> None:
        """Count only surviving rows when selected IDs have disappeared."""
        with patch.object(
            _storage,
            "read_session_list_batch",
            return_value={},
        ) as read_batch:
            result = await self.storage.list_sessions_page("user", "agent")
        read_batch.assert_awaited_once()
        self.assertEqual(result, ([], 0))

    async def test_legacy_origin_and_missing_state(self) -> None:
        """Read old records without migrations or a full payload fallback."""
        row = _from_record(SessionRow, self.records[0])
        row.id = "legacy"
        row.payload.pop("origin")
        row.payload.pop("state")
        row.payload.update(
            source_channel_id="channel",
            source_chat_id="chat",
            source_chat_name="Channel chat",
        )
        row.source = "channel"
        async with self.storage._session() as sess:
            sess.add(row)
            await sess.commit()
        items, total = await self.storage.list_sessions_page("user", "agent")
        legacy = next(item for item in items if item.session.id == "legacy")
        self.assertEqual(
            (total, legacy.session.origin, legacy.tool_call_states),
            (
                7,
                ChannelOrigin(
                    channel_id="channel",
                    chat_id="chat",
                    chat_name="Channel chat",
                ),
                [],
            ),
        )
        # Historical messages can store text as a scalar, not blocks.
        async with self.storage._session() as sess:
            stored = await sess.get(SessionRow, "legacy")
            stored.payload = {
                **stored.payload,
                "state": {
                    "context": [{"role": "assistant", "content": "text"}],
                },
            }
            await sess.commit()
        items, total = await self.storage.list_sessions_page("user", "agent")
        legacy = next(item for item in items if item.session.id == "legacy")
        self.assertEqual(legacy.tool_call_states, [])

    async def test_http_pagination_and_running_override(self) -> None:
        """Keep response shape, total, validation and live lock priority."""
        app = FastAPI()
        app.include_router(session_router)
        access = AsyncMock()
        bus = AsyncMock()
        bus.is_locked.return_value = False
        app.dependency_overrides.update(
            {
                get_storage: lambda: self.storage,
                get_current_user_id: lambda: "user",
                get_resource_access_service: lambda: access,
                get_message_bus: lambda: bus,
            },
        )
        with TestClient(app) as client:
            full = client.get("/sessions/", params={"agent_id": "agent"})
            self.assertEqual(full.status_code, 200, full.text)
            expected_views = [
                {
                    "session": {
                        **record.model_dump(mode="json", exclude={"state"}),
                        "state": record.state.model_dump(
                            mode="json",
                            include={"permission_context", "tasks_context"},
                        ),
                    },
                    "is_running": False,
                    "status": status,
                    "team": None,
                }
                for record, status in reversed(
                    list(zip(self.records, self.statuses)),
                )
            ]
            self.assertEqual(
                full.json(),
                {"sessions": expected_views, "total": 6},
            )
            for offset in (0, 2, 6, 2**63):
                response = client.get(
                    "/sessions/",
                    params={"agent_id": "agent", "limit": 2, "offset": offset},
                )
                self.assertEqual(
                    response.json(),
                    {
                        "sessions": expected_views[offset : offset + 2],
                        "total": 6,
                    },
                )
            offset_only = client.get(
                "/sessions/",
                params={"agent_id": "agent", "offset": 2},
            )
            self.assertEqual(
                offset_only.json(),
                {"sessions": expected_views[2:], "total": 6},
            )
            bus.is_locked.return_value = True
            response = client.get(
                "/sessions/",
                params={"agent_id": "agent", "limit": 1, "offset": 2},
            )
            running = dict(
                expected_views[2],
                is_running=True,
                status="running",
            )
            self.assertEqual(
                response.json(),
                {"sessions": [running], "total": 6},
            )
            for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
                response = client.get(
                    "/sessions/",
                    params={"agent_id": "agent", **params},
                )
                self.assertEqual(response.status_code, 422)
            access.resolve_agent.assert_awaited_with("user", "agent")


class SessionListDatabaseTest(IsolatedAsyncioTestCase):
    """Run dialect regressions against dedicated test databases."""

    async def _storage(
        self,
        url_variable: str,
        **engine_kwargs: str,
    ) -> AsyncSQLAlchemyStorage:
        url = environ.get(url_variable)
        if not url:
            self.skipTest(f"Set {url_variable} to an async test database URL")
        return await self.enterAsyncContext(
            AsyncSQLAlchemyStorage(url, engine_kwargs=engine_kwargs),
        )

    async def _check_page_snapshot(
        self,
        storage: AsyncSQLAlchemyStorage,
    ) -> None:
        user = uuid4().hex
        first = await storage.upsert_session(
            user,
            "a",
            SessionConfig(name="first", workspace_id="ws"),
        )
        self.addAsyncCleanup(storage.delete_session, user, "a", first.id)

        async def write_changes(_driver: object) -> None:
            second = await storage.upsert_session(
                user,
                "a",
                SessionConfig(name="second", workspace_id="ws"),
            )
            self.addAsyncCleanup(storage.delete_session, user, "a", second.id)
            await storage.upsert_session(
                user,
                "a",
                SessionConfig(name="updated", workspace_id="ws"),
                session_id=first.id,
            )

        def after_count(
            conn: sa.Connection,
            _cursor: object,
            statement: str,
            _parameters: tuple,
            _context: object,
            _many: bool,
        ) -> None:
            if "count(" in statement.lower():
                conn.connection.dbapi_connection.run_async(write_changes)

        async def page() -> tuple[list[str], int]:
            items, total = await storage.list_sessions_page(user, "a", limit=2)
            return sorted(item.session.config.name for item in items), total

        engine = storage._engine.sync_engine
        sa.event.listen(engine, "after_cursor_execute", after_count)
        try:
            self.assertEqual(await page(), (["first"], 1))
        finally:
            sa.event.remove(engine, "after_cursor_execute", after_count)
        self.assertEqual(await page(), (["second", "updated"], 2))

    async def test_sqlite_page_snapshot(self) -> None:
        """Let an independent WAL writer commit while the reader is active."""
        # Keep the database until all registered row cleanups have run.
        directory = self.enterContext(
            TemporaryDirectory(),  # pylint: disable=consider-using-with
        )
        url = URL.create(
            "sqlite+aiosqlite",
            database=str(Path(directory) / "snapshot.db"),
        )
        storage = await self.enterAsyncContext(
            AsyncSQLAlchemyStorage(url.render_as_string()),
        )
        async with storage._engine.connect() as conn:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await self._check_page_snapshot(storage)

    async def test_postgresql_page_snapshot(self) -> None:
        """Count, membership and details must reflect the same snapshot."""
        storage = await self._storage("AGENTSCOPE_TEST_POSTGRESQL_URL")
        await self._check_page_snapshot(storage)

    async def test_postgresql_autocommit_lists(self) -> None:
        """Both list modes can use savepoints on an autocommit engine."""
        storage = await self._storage(
            "AGENTSCOPE_TEST_POSTGRESQL_URL",
            isolation_level="AUTOCOMMIT",
        )
        user = uuid4().hex
        config = SessionConfig(name="session", workspace_id="ws")
        ordinary = await storage.upsert_session(user, "a", config)
        self.addAsyncCleanup(storage.delete_session, user, "a", ordinary.id)
        ordinary_item = SessionListItem.from_record(ordinary)
        self.assertEqual(
            await storage.list_sessions_page(user, "a"),
            ([ordinary_item], 1),
        )
        history = await storage.upsert_session(
            user,
            "a",
            config,
            AgentState(summary="before\x00after"),
        )
        self.addAsyncCleanup(storage.delete_session, user, "a", history.id)
        history_item = SessionListItem.from_record(history)
        self.assertEqual(
            await storage.list_sessions_page(user, "a"),
            ([history_item, ordinary_item], 2),
        )
        self.assertEqual(
            await storage.list_sessions_page(user, "a", limit=1),
            ([history_item], 2),
        )

    async def test_mysql_page_snapshot(self) -> None:
        """Keep a page consistent even with a read-committed engine."""
        storage = await self._storage(
            "AGENTSCOPE_TEST_MYSQL_URL",
            isolation_level="READ COMMITTED",
        )
        await self._check_page_snapshot(storage)

    async def test_postgresql_nul_and_legacy_content(self) -> None:
        """A NUL in either row must preserve the normal batch projection."""
        storage = await self._storage("AGENTSCOPE_TEST_POSTGRESQL_URL")
        user = uuid4().hex
        config = SessionConfig(name="legacy", workspace_id="ws")
        legacy = await storage.upsert_session(user, "a", config)
        pending = await storage.upsert_session(
            user,
            "a",
            config,
            AgentState(context=[SessionListTest._assistant("submitted")]),
        )

        async def cleanup() -> None:
            async with storage._session() as session:
                await session.execute(
                    sa.delete(SessionRow).where(SessionRow.user_id == user),
                )
                await session.commit()

        self.addAsyncCleanup(cleanup)
        async with storage._session() as session:
            row = await session.get(SessionRow, legacy.id)
            row.payload = {
                **row.payload,
                "state": {
                    **row.payload["state"],
                    "context": [
                        {"role": "assistant", "name": "a", "content": "text"},
                    ],
                },
            }
            await session.commit()
        expected = await storage.list_sessions_page(user, "a", limit=2)
        for nul_id in (legacy.id, pending.id):
            with self.subTest(nul_id=nul_id):
                async with storage._session() as session:
                    for record in (legacy, pending):
                        row = await session.get(SessionRow, record.id)
                        summary = "\x00" if record.id == nul_id else ""
                        row.payload = {
                            **row.payload,
                            "state": {
                                **row.payload["state"],
                                "summary": summary,
                            },
                        }
                    await session.commit()
                self.assertEqual(
                    await storage.list_sessions_page(user, "a", limit=2),
                    expected,
                )

    async def test_postgresql_nul_in_history(self) -> None:
        """Preserve NUL on full reads and trim it on paginated reads."""
        storage = await self._storage("AGENTSCOPE_TEST_POSTGRESQL_URL")
        self.assertEqual(storage._engine.dialect.name, "postgresql")
        user = uuid4().hex
        config = SessionConfig(name="history", workspace_id="ws")
        state = AgentState(
            context=[
                UserMsg(name="u", content="before\x00after"),
                SessionListTest._assistant("asking"),
            ],
        )
        history = await storage.upsert_session(user, "a", config, state)
        self.addAsyncCleanup(storage.delete_session, user, "a", history.id)
        recent = await storage.upsert_session(user, "a", config)
        self.addAsyncCleanup(storage.delete_session, user, "a", recent.id)
        self.assertEqual(
            await storage.get_session(user, "a", history.id),
            history,
        )
        for offset, record in enumerate((recent, history)):
            with self.subTest(offset=offset):
                self.assertEqual(
                    await storage.list_sessions_page(
                        user,
                        "a",
                        limit=1,
                        offset=offset,
                    ),
                    ([SessionListItem.from_record(record)], 2),
                )

    async def test_mysql_driver_tail_states(self) -> None:
        """Extract tail states through a mysql URL, including on MariaDB."""
        storage = await self._storage("AGENTSCOPE_TEST_MYSQL_URL")
        self.assertEqual(storage._engine.dialect.name, "mysql")
        user = uuid4().hex
        record = await storage.upsert_session(
            user,
            "a",
            SessionConfig(name="tail", workspace_id="ws"),
        )
        self.addAsyncCleanup(storage.delete_session, user, "a", record.id)
        for state in (None, "asking", "submitted"):
            with self.subTest(state=state):
                context = []
                if state:
                    context = [
                        UserMsg(name="u", content="first"),
                        SessionListTest._assistant(state),
                    ]
                await storage.update_session_state(
                    user,
                    "a",
                    record.id,
                    AgentState(context=context),
                )
                full = await storage.get_session(user, "a", record.id)
                self.assertIsNotNone(full)
                self.assertEqual(
                    await storage.list_sessions_page(user, "a"),
                    ([SessionListItem.from_record(full)], 1),
                )


class SessionListMigrationTest(IsolatedAsyncioTestCase):
    """Existing and fresh DBs acquire the same useful list index."""

    async def test_upgrade_and_downgrade(self) -> None:
        """Upgrade populated tables and verify the ordered query plan."""
        with TemporaryDirectory(
            prefix="agentscope-session-%sql-",
        ) as directory:
            url = URL.create(
                "sqlite+aiosqlite",
                database=str(Path(directory) / "test.db"),
            ).render_as_string(hide_password=False)
            config = Config()
            config.set_main_option(
                "script_location",
                str(Path(_storage.__file__).parent / "_alembic"),
            )
            # ConfigParser treats URL percent escapes as interpolation.
            config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
            self.assertEqual(config.get_main_option("sqlalchemy.url"), url)
            await asyncio.to_thread(
                command.upgrade,
                config,
                "0005_voice_profiles",
            )
            async with AsyncSQLAlchemyStorage(
                url,
                create_tables=False,
            ) as storage:
                record = await storage.upsert_session(
                    "user",
                    "agent",
                    config=SessionConfig(name="kept", workspace_id="ws"),
                )
                await asyncio.to_thread(command.upgrade, config, "head")
                async with storage._engine.connect() as conn:
                    indexes = await conn.run_sync(
                        lambda sync: sa.inspect(sync).get_indexes("sessions"),
                    )
                    index = next(
                        item
                        for item in indexes
                        if item["name"] == "ix_sessions_user_agent_created_id"
                    )
                    self.assertEqual(
                        index["column_names"],
                        ["user_id", "agent_id", "created_at", "id"],
                    )
                    plan = await conn.execute(
                        sa.text(
                            "EXPLAIN QUERY PLAN SELECT * FROM sessions "
                            "WHERE user_id=:user AND agent_id=:agent "
                            "ORDER BY created_at DESC, id DESC LIMIT 10",
                        ),
                        {"user": "user", "agent": "agent"},
                    )
                    detail = " ".join(row[-1] for row in plan)
                    self.assertIn("ix_sessions_user_agent_created_id", detail)
                    self.assertNotIn("TEMP B-TREE", detail)
                await asyncio.to_thread(
                    command.downgrade,
                    config,
                    "0005_voice_profiles",
                )
                self.assertEqual(
                    await storage.get_session("user", "agent", record.id),
                    record,
                )
