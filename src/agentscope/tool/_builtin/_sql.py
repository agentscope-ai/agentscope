# -*- coding: utf-8 -*-
"""The SQL database tools in agentscope."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, List

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .._base import ToolBase, ToolMiddlewareBase
from .._response import ToolChunk

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


# Leading keywords of statements that only read data.
_READ_ONLY_KEYWORDS = frozenset(
    {"select", "with", "explain", "show", "describe", "desc", "values"},
)

# Keywords that may modify data or schema anywhere in a statement.
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|merge|upsert|create|alter|drop|truncate|"
    r"rename|grant|revoke|attach|detach|vacuum|reindex|copy|call|exec|"
    r"execute|lock|into)\b",
    re.IGNORECASE,
)

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


def _strip_sql(query: str) -> str:
    """Remove comments and string literals so keyword checks only see
    SQL syntax, then trim whitespace and trailing semicolons."""
    stripped = _COMMENT.sub(" ", query)
    stripped = _STRING_LITERAL.sub("''", stripped)
    return stripped.strip().rstrip(";").strip()


def _is_read_only_sql(query: str) -> bool:
    """Conservatively decide whether a SQL statement is read-only.

    A statement counts as read-only only if it is a single statement,
    starts with a read keyword and contains no write keyword (which
    catches data-modifying CTEs such as ``WITH x AS (DELETE ...)``).
    Anything ambiguous is treated as a write.
    """
    stripped = _strip_sql(query)
    if not stripped or ";" in stripped:
        return False
    first = stripped.split(None, 1)[0].lower()
    if first not in _READ_ONLY_KEYWORDS:
        return False
    return _WRITE_KEYWORDS.search(stripped) is None


def _format_cell(value: Any, max_chars: int) -> str:
    """Render a single cell for a Markdown table."""
    if value is None:
        text = "NULL"
    elif isinstance(value, (bytes, bytearray, memoryview)):
        text = f"<{len(bytes(value))} bytes>"
    else:
        text = str(value)
    text = text.replace("|", "\\|").replace("\n", " ")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def _format_table(
    columns: List[str],
    rows: List[tuple],
    max_chars: int,
) -> str:
    """Render the rows as a Markdown table."""
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_format_cell(v, max_chars) for v in row) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def _create_engine(url: str) -> "AsyncEngine":
    """Create an async SQLAlchemy engine, with a helpful import error."""
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError as e:
        raise ImportError(
            "The SQL tools require SQLAlchemy. Install it with "
            "`pip install agentscope[storage-sql]` plus an async driver "
            "such as aiosqlite, asyncpg or aiomysql.",
        ) from e
    return create_async_engine(url)


def _error(text: str) -> ToolChunk:
    """Build an error chunk."""
    return ToolChunk(
        content=[TextBlock(text=text)],
        state=ToolResultState.ERROR,
        is_last=True,
    )


class SQLQuery(ToolBase):
    """Run SQL statements against a relational database.

    Works with any database supported by SQLAlchemy's asyncio extension
    (SQLite via ``aiosqlite``, PostgreSQL via ``asyncpg``, MySQL via
    ``aiomysql``/``asyncmy``, ...). By default the tool is read-only:
    write statements are denied up front, and every query runs inside a
    transaction that is rolled back afterwards as a second line of
    defence.

    Example:
        ```python
        from agentscope.tool import SQLQuery, Toolkit

        toolkit = Toolkit(tools=[SQLQuery(url="sqlite+aiosqlite:///shop.db")])
        ```
    """

    name: str = "SQLQuery"
    """The tool name presented to the agent."""

    description: str = """Execute a single SQL statement against the
connected database and return the result as a Markdown table.

Usage:
- Only one statement per call; do not chain statements with ';'.
- Inspect the schema with the SQLSchema tool before writing queries
  instead of guessing table or column names.
- Results are truncated to a maximum number of rows; add filters,
  aggregations or a LIMIT clause instead of fetching whole tables.
- Unless the tool has been configured to allow writes, only read
  statements (SELECT, WITH, EXPLAIN, SHOW, ...) are permitted."""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The SQL statement to execute.",
            },
        },
        "required": ["query"],
    }

    is_mcp: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        url: str | None = None,
        engine: AsyncEngine | None = None,
        read_only: bool = True,
        max_rows: int = 100,
        max_cell_chars: int = 200,
        timeout: float = 30.0,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the SQL query tool.

        Args:
            url (`str | None`, optional):
                An async SQLAlchemy database URL, e.g.
                ``"sqlite+aiosqlite:///data.db"`` or
                ``"postgresql+asyncpg://user:pass@host/db"``. Exactly one
                of ``url`` and ``engine`` must be given.
            engine (`AsyncEngine | None`, optional):
                An existing async engine to reuse. The caller keeps
                ownership of it and is responsible for disposing it.
            read_only (`bool`, defaults to `True`):
                Whether to reject statements that may modify data or
                schema. When ``True`` every statement is also rolled back
                after execution.
            max_rows (`int`, defaults to `100`):
                The maximum number of rows returned to the agent.
            max_cell_chars (`int`, defaults to `200`):
                The maximum number of characters shown per cell.
            timeout (`float`, defaults to `30.0`):
                The maximum time in seconds a statement may run.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
        """
        super().__init__(middlewares=middlewares)
        if (url is None) == (engine is None):
            raise ValueError("Exactly one of `url` and `engine` is required.")
        if max_rows < 1:
            raise ValueError("`max_rows` must be at least 1.")

        self._owns_engine = engine is None
        self._engine = engine or _create_engine(url)  # type: ignore[arg-type]
        self.read_only = read_only
        self.is_read_only = read_only
        self.max_rows = max_rows
        self.max_cell_chars = max_cell_chars
        self.timeout = timeout

    @property
    def engine(self) -> "AsyncEngine":
        """The underlying async SQLAlchemy engine."""
        return self._engine

    async def close(self) -> None:
        """Dispose the engine if it was created by this tool."""
        if self._owns_engine:
            await self._engine.dispose()

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        """Decide whether the given statement is read-only."""
        return _is_read_only_sql(tool_input.get("query", ""))

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for executing the SQL statement.

        Write statements are denied outright when the tool is read-only.
        Everything else is passed through to the permission engine, which
        uses :meth:`check_read_only` to decide whether to ask the user.
        """
        if self.read_only and not await self.check_read_only(tool_input):
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="SQLQuery is configured as read-only; only a single "
                "read statement (SELECT, WITH, EXPLAIN, SHOW, ...) is "
                "allowed.",
                decision_reason="Read-only SQL tool",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="SQL statement execution.",
        )

    async def call(self, query: str) -> ToolChunk:  # type: ignore[override]
        """Execute the SQL statement and return the result.

        Args:
            query (`str`):
                The SQL statement to execute.

        Returns:
            `ToolChunk`:
                The rows as a Markdown table, the number of affected rows
                for writes, or an error chunk if the statement is rejected
                or fails.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        if not query.strip():
            return _error("The SQL statement is empty.")
        if self.read_only and not _is_read_only_sql(query):
            return _error(
                "Rejected: this tool is read-only and the statement is not "
                "a single read statement.",
            )

        try:
            output = await asyncio.wait_for(
                self._execute(text(query)),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            return _error(
                f"The SQL statement timed out after {self.timeout} seconds.",
            )
        except SQLAlchemyError as e:
            # ``orig`` holds the driver error, which is far more useful to
            # the agent than SQLAlchemy's wrapper text with its doc links.
            return _error(f"SQL error: {getattr(e, 'orig', None) or e}")

        return ToolChunk(
            content=[TextBlock(text=output)],
            state=ToolResultState.RUNNING,
            is_last=True,
        )

    async def _execute(self, statement: Any) -> str:
        """Run the statement in a transaction and render the result."""
        async with self._engine.connect() as conn:
            trans = await conn.begin()
            try:
                result = await conn.execute(statement)
                if not result.returns_rows:
                    output = (
                        f"Statement executed. Rows affected: "
                        f"{result.rowcount}"
                    )
                else:
                    columns = list(result.keys())
                    rows = [tuple(r) for r in result.fetchmany(self.max_rows)]
                    truncated = result.fetchone() is not None
                    result.close()
                    output = self._render_rows(columns, rows, truncated)
            except BaseException:
                await trans.rollback()
                raise
            if self.read_only:
                await trans.rollback()
            else:
                await trans.commit()
        return output

    def _render_rows(
        self,
        columns: List[str],
        rows: List[tuple],
        truncated: bool,
    ) -> str:
        """Render query rows with a summary line."""
        if not rows:
            return "Query returned no rows. Columns: " + ", ".join(columns)
        table = _format_table(columns, rows, self.max_cell_chars)
        if truncated:
            return (
                f"{table}\n\nShowing the first {len(rows)} rows; more rows "
                "were omitted. Refine the query to narrow the result."
            )
        return f"{table}\n\n{len(rows)} row(s) returned."


class SQLSchema(ToolBase):
    """List the tables of a relational database, or describe the columns
    of specific tables, so an agent can write correct SQL."""

    name: str = "SQLSchema"
    """The tool name presented to the agent."""

    description: str = """Inspect the schema of the connected database.

- Call without `tables` to list all table and view names.
- Pass `tables` to get the columns (name, type, nullable, default),
  primary key and foreign keys of those tables."""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of the tables to describe. Omit to "
                "list all tables.",
            },
        },
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        url: str | None = None,
        engine: AsyncEngine | None = None,
        schema: str | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the SQL schema tool.

        Args:
            url (`str | None`, optional):
                An async SQLAlchemy database URL. Exactly one of ``url``
                and ``engine`` must be given.
            engine (`AsyncEngine | None`, optional):
                An existing async engine to reuse, e.g.
                ``SQLQuery(...).engine`` to share one connection pool.
            schema (`str | None`, optional):
                The database schema to inspect. Defaults to the
                connection's default schema.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
        """
        super().__init__(middlewares=middlewares)
        if (url is None) == (engine is None):
            raise ValueError("Exactly one of `url` and `engine` is required.")
        self._owns_engine = engine is None
        self._engine = engine or _create_engine(url)  # type: ignore[arg-type]
        self.schema = schema

    async def close(self) -> None:
        """Dispose the engine if it was created by this tool."""
        if self._owns_engine:
            await self._engine.dispose()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Schema inspection is read-only; defer to the engine."""
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="SQL schema inspection is read-only.",
        )

    async def call(  # type: ignore[override]
        self,
        tables: List[str] | None = None,
    ) -> ToolChunk:
        """List tables, or describe the given tables.

        Args:
            tables (`List[str] | None`, optional):
                The tables to describe. ``None`` or empty lists all tables.

        Returns:
            `ToolChunk`:
                The schema description, or an error chunk.
        """
        from sqlalchemy import inspect
        from sqlalchemy.exc import SQLAlchemyError

        def _inspect(sync_conn: Any) -> str:
            inspector = inspect(sync_conn)
            names = inspector.get_table_names(schema=self.schema)
            views = inspector.get_view_names(schema=self.schema)
            if not tables:
                lines = [f"Tables: {', '.join(names) or '(none)'}"]
                if views:
                    lines.append(f"Views: {', '.join(views)}")
                return "\n".join(lines)

            known = set(names) | set(views)
            sections = []
            for table in tables:
                if table not in known:
                    sections.append(f"## {table}\nTable not found.")
                    continue
                sections.append(self._describe(inspector, table))
            return "\n\n".join(sections)

        try:
            async with self._engine.connect() as conn:
                output = await conn.run_sync(_inspect)
        except SQLAlchemyError as e:
            return _error(f"SQL error: {getattr(e, 'orig', None) or e}")

        return ToolChunk(
            content=[TextBlock(text=output)],
            state=ToolResultState.RUNNING,
            is_last=True,
        )

    def _describe(self, inspector: Any, table: str) -> str:
        """Describe the columns and keys of one table."""
        columns = inspector.get_columns(table, schema=self.schema)
        rows = [
            (
                c["name"],
                str(c["type"]),
                "YES" if c.get("nullable", True) else "NO",
                c.get("default"),
            )
            for c in columns
        ]
        lines = [
            f"## {table}",
            _format_table(
                ["column", "type", "nullable", "default"],
                rows,
                200,
            ),
        ]
        pk = inspector.get_pk_constraint(table, schema=self.schema)
        if pk and pk.get("constrained_columns"):
            lines.append(
                "Primary key: " + ", ".join(pk["constrained_columns"]),
            )
        for fk in inspector.get_foreign_keys(table, schema=self.schema):
            lines.append(
                f"Foreign key: ({', '.join(fk['constrained_columns'])}) -> "
                f"{fk['referred_table']}"
                f"({', '.join(fk['referred_columns'])})",
            )
        return "\n".join(lines)
