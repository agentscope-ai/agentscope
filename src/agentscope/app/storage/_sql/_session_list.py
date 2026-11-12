# -*- coding: utf-8 -*-
"""Dialect-specific JSON projections for read-only session lists."""
import json

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.sql.elements import ColumnElement

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
        tail = "last" if dialect == "mysql" else "#-1"
        role = sa.func.json_extract(
            payload,
            f"$.state.context[{tail}].role",
        )
        if dialect == "mysql":
            role = sa.func.json_unquote(role)
            content = sa.func.json_extract(
                payload,
                "$.state.context[last].content",
            )
            for state in ("asking", "submitted"):
                flags.append(
                    sa.func.json_contains(
                        content,
                        json.dumps({"type": "tool_call", "state": state}),
                    ),
                )
        else:
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
