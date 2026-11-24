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


def session_list_columns(dialect: str) -> list[ColumnElement]:
    """Exclude heavy state and return two tail tool-state flags.

    No conversation text, tool inputs or tool results leave the database.
    Other state fields retain the existing list response semantics.
    """
    payload = SessionRow.payload
    removed = ("context", "summary", "tool_context")
    flags = []
    if dialect == "postgresql":
        document = sa.cast(payload, JSONB)
        trimmed = document
        for field in removed:
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
        for state in ("asking", "submitted"):
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
            *[f"$.state.{field}" for field in removed],
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
            for state in ("asking", "submitted"):
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
            for state in ("asking", "submitted"):
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
            for flag, name in zip(flags, ("asking", "submitted"))
        ],
    ]


async def read_session_list_batch(
    session: AsyncSession,
    dialect: str,
    *criteria: ColumnElement[bool],
) -> dict[str, SessionListItem]:
    """Read the selected rows, including historical PostgreSQL JSON."""
    columns = [c for c in SessionRow.__table__.columns if c.name != "payload"]
    query = sa.select(*columns, *session_list_columns(dialect)).where(
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
            rows = await session.scalars(
                sa.select(SessionRow).where(*criteria),
            )
            return {
                row.id: SessionListItem.from_record(
                    _to_record(row, SessionRecord),
                )
                for row in rows
            }
    else:
        result = await session.execute(query)

    items = {}
    for row in result.mappings():
        values = dict(row)
        states = [s for s in ("asking", "submitted") if values.pop(s)]
        items[row.id] = SessionListItem(
            session=_to_record(SessionRow(**values), SessionRecord),
            tool_call_states=states,
        )
    return items
