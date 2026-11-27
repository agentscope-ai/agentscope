# -*- coding: utf-8 -*-
"""Dialect-specific JSON projections for read-only session lists."""
import json

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .._model._session import SessionRecord
from .._model._session_list import SessionListItem
from ._mappers import _to_record
from ._tables import SessionRow


_HEAVY_FIELDS = ("context", "summary", "tool_context")
_TOOL_STATES = ("asking", "submitted")
_LIST_COLUMNS = tuple(
    col for col in SessionRow.__table__.columns if col.name != "payload"
)


def session_list_columns(dialect: str) -> list[ColumnElement]:
    """Exclude heavy state and return two tail tool-state flags.

    No conversation text, tool inputs or tool results leave the database.
    Other state fields retain the existing list response semantics.
    """
    payload = SessionRow.payload
    flags = []
    if dialect == "postgresql":
        document = sa.cast(payload, JSONB)
        trimmed = document
        for field in _HEAVY_FIELDS:
            trimmed = trimmed.op("#-")(array(["state", field]))
        role = sa.func.jsonb_extract_path_text(
            document,
            "state",
            "context",
            "-1",
            "role",
        )
        content = sa.func.jsonb_extract_path(
            document,
            "state",
            "context",
            "-1",
            "content",
        )
        for state in _TOOL_STATES:
            flags.append(
                content.op("@>")(
                    sa.cast(
                        sa.literal(
                            json.dumps(
                                [{"type": "tool_call", "state": state}],
                            ),
                        ),
                        JSONB,
                    ),
                ),
            )
    else:
        trimmed = sa.func.json_remove(
            payload,
            *[f"$.state.{field}" for field in _HEAVY_FIELDS],
        )
        if dialect == "mysql":
            # MariaDB before 10.9 only accepts numeric array indexes.
            length = sa.func.json_length(payload, "$.state.context")
            tail = sa.func.greatest(length, 1) - 1
            role = sa.func.json_unquote(
                sa.func.json_extract(
                    payload,
                    sa.func.concat("$.state.context[", tail, "].role"),
                ),
            )
            content = sa.func.json_extract(
                payload,
                sa.func.concat("$.state.context[", tail, "].content"),
            )
            for state in _TOOL_STATES:
                flags.append(
                    sa.func.json_contains(
                        content,
                        json.dumps({"type": "tool_call", "state": state}),
                    ),
                )
        else:
            role = sa.func.json_extract(payload, "$.state.context[#-1].role")
            blocks = sa.func.json_each(
                payload,
                "$.state.context[#-1].content",
            ).table_valued("value", "type")
            block = sa.case(
                (blocks.c.type == "object", blocks.c.value),
                else_="{}",
            )
            for state in _TOOL_STATES:
                flags.append(
                    sa.select(1)
                    .select_from(blocks)
                    .where(
                        sa.func.json_extract(block, "$.type") == "tool_call",
                        sa.func.json_extract(block, "$.state") == state,
                    )
                    .correlate(SessionRow)
                    .exists(),
                )
    return [
        sa.type_coerce(trimmed, sa.JSON).label("payload"),
        *[
            sa.and_(role == "assistant", flag).label(name)
            for flag, name in zip(flags, _TOOL_STATES)
        ],
    ]


def _project_raw_payload(payload: dict) -> tuple[dict, list[str]]:
    """Mirror the JSON projection before validating a legacy record."""
    state = payload.get("state")
    if not isinstance(state, dict):
        return payload, []
    trimmed = {k: v for k, v in state.items() if k not in _HEAVY_FIELDS}
    payload = {**payload, "state": trimmed}
    context = state.get("context")
    if not isinstance(context, list) or not context:
        return payload, []
    tail = context[-1]
    if not isinstance(tail, dict) or tail.get("role") != "assistant":
        return payload, []
    content = tail.get("content")
    if not isinstance(content, list):
        return payload, []
    states = [
        block.get("state")
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_call"
    ]
    return payload, [name for name in _TOOL_STATES if name in states]


def _list_item(values: dict) -> SessionListItem:
    """Reconstruct a list item from a projected or raw result mapping."""
    if "asking" in values:
        states = [s for s in _TOOL_STATES if values.pop(s)]
    else:
        values["payload"], states = _project_raw_payload(values["payload"])
    return SessionListItem(
        session=_to_record(SessionRow(**values), SessionRecord),
        tool_call_states=states,
    )


async def read_sqlite_session_page(
    session: AsyncSession,
    *criteria: ColumnElement[bool],
    limit: int | None,
    offset: int,
) -> tuple[list[SessionListItem], int]:
    """Read one SQLite snapshot, including the count for an empty page."""
    count = sa.select(sa.func.count().label("total")).select_from(SessionRow)
    count = count.where(*criteria)
    # SQLite cannot bind offsets larger than its signed integer range.
    if offset > 2**63 - 1:
        return [], (await session.execute(count)).scalar_one()
    total_rows = count.subquery()
    page_ids = (
        sa.select(SessionRow.id)
        .where(*criteria)
        .order_by(SessionRow.created_at.desc(), SessionRow.id.desc())
        .limit(limit)
        .offset(offset)
        .subquery()
    )
    query = sa.select(
        total_rows.c.total,
        *_LIST_COLUMNS,
        *session_list_columns("sqlite"),
    ).select_from(
        total_rows.outerjoin(page_ids, total_rows.c.total > offset).outerjoin(
            SessionRow,
            SessionRow.id == page_ids.c.id,
        ),
    )
    items = []
    total = 0
    connection = await session.connection()
    async with connection.stream(
        query,
        execution_options={"yield_per": 200},
    ) as rows:
        async for row in rows.mappings():
            values = dict(row)
            total = values.pop("total")
            if row.id is not None:
                items.append(_list_item(values))
    # Only sort IDs in SQL. The outer joins need not preserve their order.
    items.sort(
        key=lambda item: (item.session.created_at, item.session.id),
        reverse=True,
    )
    return items, total


async def read_session_list_batch(
    session: AsyncSession,
    dialect: str,
    *criteria: ColumnElement[bool],
) -> dict[str, SessionListItem]:
    """Read the selected rows, including historical PostgreSQL JSON."""
    query = sa.select(*_LIST_COLUMNS, *session_list_columns(dialect)).where(
        *criteria,
    )
    if dialect == "postgresql":
        try:
            async with session.begin_nested():
                result = await session.execute(query)
        except sa.exc.DBAPIError as exc:
            # JSON accepts escaped NUL, but the JSONB projection rejects it.
            if getattr(exc.orig, "sqlstate", None) != "22P05":
                raise
            result = await session.execute(
                sa.select(*_LIST_COLUMNS, SessionRow.payload).where(*criteria),
            )
    else:
        result = await session.execute(query)

    return {row.id: _list_item(dict(row)) for row in result.mappings()}
