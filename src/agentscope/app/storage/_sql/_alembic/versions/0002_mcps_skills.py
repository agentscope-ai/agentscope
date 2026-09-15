# -*- coding: utf-8 -*-
"""Installed-MCP and installed-skill tables.

Revision ID: 0002_mcps_skills
Revises: 0001_initial
Create Date: 2026-08-01 09:34:28.407719

Both tables are new, so there is nothing to backfill — the library has
never been stored on SQL. ``(user_id, name)`` is unique on each because
the workspace relation joins on the name.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_mcps_skills"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``mcps`` and ``skills`` tables."""
    for table in ("mcps", "skills"):
        op.create_table(
            table,
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("id", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "user_id",
                "name",
                name=f"uq_{table}_user_name",
            ),
        )
        with op.batch_alter_table(table, schema=None) as batch_op:
            for column in ("created_at", "updated_at", "user_id"):
                batch_op.create_index(
                    batch_op.f(f"ix_{table}_{column}"),
                    [column],
                    unique=False,
                )


def downgrade() -> None:
    """Drop both tables created by :func:`upgrade`."""
    for table in ("skills", "mcps"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for column in ("user_id", "updated_at", "created_at"):
                batch_op.drop_index(batch_op.f(f"ix_{table}_{column}"))
        op.drop_table(table)
