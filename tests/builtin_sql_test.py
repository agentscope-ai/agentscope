# -*- coding: utf-8 -*-
"""SQLQuery / SQLSchema tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import ToolResultState
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
)
from agentscope.tool import SQLQuery, SQLSchema
from agentscope.tool._builtin._sql import _is_read_only_sql


class SQLReadOnlyClassifierTest(IsolatedAsyncioTestCase):
    """The read-only SQL classifier test case."""

    async def test_read_statements(self) -> None:
        """Plain read statements are read-only."""
        for query in [
            "SELECT * FROM users",
            "  select id from users;  ",
            "WITH t AS (SELECT 1) SELECT * FROM t",
            "EXPLAIN SELECT 1",
            "-- leading comment\nSELECT 1",
            "SELECT 'delete from users' AS s",
            "SELECT name FROM users ORDER BY name DESC",
        ]:
            self.assertTrue(_is_read_only_sql(query), query)

    async def test_write_statements(self) -> None:
        """Writes, multi-statements and data-modifying CTEs are rejected."""
        for query in [
            "",
            "INSERT INTO users VALUES (1)",
            "update users set name = 'x'",
            "DROP TABLE users",
            "SELECT 1; DROP TABLE users",
            "WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d",
            "SELECT * INTO backup FROM users",
            "PRAGMA journal_mode=WAL",
        ]:
            self.assertFalse(_is_read_only_sql(query), query)


class SQLToolTest(IsolatedAsyncioTestCase):
    """The SQL tools test case."""

    async def asyncSetUp(self) -> None:
        """Create a SQLite database with sample data."""
        self.temp_dir = tempfile.mkdtemp()
        self.url = "sqlite+aiosqlite:///" + os.path.join(
            self.temp_dir,
            "test.db",
        )
        self.writer = SQLQuery(url=self.url, read_only=False)
        for stmt in [
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER "
            "REFERENCES users(id), total REAL)",
            "INSERT INTO users (id, name) VALUES (1, 'alice'), (2, 'bob'), "
            "(3, 'carol')",
            "INSERT INTO orders VALUES (1, 1, 9.5), (2, 1, NULL)",
        ]:
            res = await self.writer(query=stmt)
            self.assertEqual(res.state, ToolResultState.RUNNING)
        self.reader = SQLQuery(url=self.url, max_rows=2)
        self.schema = SQLSchema(engine=self.reader.engine)

    async def asyncTearDown(self) -> None:
        """Dispose engines and clean up temporary files."""
        import shutil

        await self.writer.close()
        await self.reader.close()
        await self.schema.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_constructor_validation(self) -> None:
        """Exactly one of url and engine must be given."""
        with self.assertRaises(ValueError):
            SQLQuery()
        with self.assertRaises(ValueError):
            SQLQuery(url=self.url, engine=self.reader.engine)
        with self.assertRaises(ValueError):
            SQLSchema()

    async def test_select_returns_markdown_table(self) -> None:
        """A SELECT returns a Markdown table with NULLs rendered."""
        res = await self.reader(
            query="SELECT id, total FROM orders ORDER BY id",
        )
        self.assertEqual(res.state, ToolResultState.RUNNING)
        self.assertEqual(
            res.content[0].text,
            "| id | total |\n| --- | --- |\n| 1 | 9.5 |\n| 2 | NULL |"
            "\n\n2 row(s) returned.",
        )

    async def test_select_truncates_rows(self) -> None:
        """Results beyond max_rows are truncated with a hint."""
        res = await self.reader(query="SELECT name FROM users ORDER BY id")
        text = res.content[0].text
        self.assertIn("| alice |", text)
        self.assertIn("| bob |", text)
        self.assertNotIn("carol", text)
        self.assertIn("Showing the first 2 rows", text)

    async def test_empty_result(self) -> None:
        """An empty result still reports the columns."""
        res = await self.reader(query="SELECT id, name FROM users WHERE 0")
        self.assertEqual(
            res.content[0].text,
            "Query returned no rows. Columns: id, name",
        )

    async def test_read_only_rejects_writes(self) -> None:
        """The read-only tool refuses write statements."""
        res = await self.reader(query="DELETE FROM users")
        self.assertEqual(res.state, ToolResultState.ERROR)
        count = await self.reader(query="SELECT COUNT(*) AS n FROM users")
        self.assertIn("| 3 |", count.content[0].text)

    async def test_sql_error(self) -> None:
        """Driver errors surface as an error chunk."""
        res = await self.reader(query="SELECT * FROM missing_table")
        self.assertEqual(res.state, ToolResultState.ERROR)
        self.assertIn("missing_table", res.content[0].text)

    async def test_write_mode_commits(self) -> None:
        """With read_only=False writes are committed."""
        res = await self.writer(query="DELETE FROM orders WHERE id = 2")
        self.assertIn("Rows affected: 1", res.content[0].text)
        count = await self.reader(query="SELECT COUNT(*) AS n FROM orders")
        self.assertIn("| 1 |", count.content[0].text)

    async def test_permissions(self) -> None:
        """Read-only tools deny writes; reads pass through."""
        ctx = PermissionContext()
        deny = await self.reader.check_permissions(
            {"query": "DROP TABLE users"},
            ctx,
        )
        self.assertEqual(deny.behavior, PermissionBehavior.DENY)
        ok = await self.reader.check_permissions(
            {"query": "SELECT 1"},
            ctx,
        )
        self.assertEqual(ok.behavior, PermissionBehavior.PASSTHROUGH)
        write = await self.writer.check_permissions(
            {"query": "DROP TABLE users"},
            ctx,
        )
        self.assertEqual(write.behavior, PermissionBehavior.PASSTHROUGH)
        self.assertFalse(
            await self.writer.check_read_only({"query": "DROP TABLE users"}),
        )
        self.assertTrue(
            await self.writer.check_read_only({"query": "SELECT 1"}),
        )

    async def test_schema_lists_tables(self) -> None:
        """SQLSchema without arguments lists the tables."""
        res = await self.schema()
        self.assertEqual(res.content[0].text, "Tables: orders, users")

    async def test_schema_describes_tables(self) -> None:
        """SQLSchema describes columns, primary and foreign keys."""
        res = await self.schema(tables=["orders", "nope"])
        text = res.content[0].text
        self.assertIn("## orders", text)
        self.assertIn("| user_id | INTEGER | YES | NULL |", text)
        self.assertIn("Primary key: id", text)
        self.assertIn("Foreign key: (user_id) -> users(id)", text)
        self.assertIn("## nope\nTable not found.", text)
