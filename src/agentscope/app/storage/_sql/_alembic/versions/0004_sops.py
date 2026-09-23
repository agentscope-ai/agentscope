# -*- coding: utf-8 -*-
"""SOP and SOP run tables.

Revision ID: 0004_sops
Revises: 0003_channels
Create Date: 2026-09-14 00:00:00.000000

Both tables are new, so there is nothing to backfill. ``phase`` is a
copy of the run state's derived phase, promoted to a column so a
listing can filter on it — "which of my runs is waiting on someone" —
without loading and replaying every run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_sops"
down_revision: Union[str, None] = "0003_channels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``sops`` and ``sop_runs`` tables."""
    op.create_table(
        "sops",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sops", schema=None) as batch_op:
        for column in ("created_at", "updated_at", "user_id"):
            batch_op.create_index(
                batch_op.f(f"ix_sops_{column}"),
                [column],
                unique=False,
            )

    op.create_table(
        "sop_runs",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("sop_id", sa.String(length=255), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sop_runs", schema=None) as batch_op:
        for column in (
            "created_at",
            "updated_at",
            "user_id",
            "sop_id",
            "phase",
        ):
            batch_op.create_index(
                batch_op.f(f"ix_sop_runs_{column}"),
                [column],
                unique=False,
            )
        batch_op.create_index(
            "ix_sop_runs_user_sop",
            ["user_id", "sop_id"],
            unique=False,
        )


def downgrade() -> None:
    """Drop the ``sop_runs`` and ``sops`` tables."""
    with op.batch_alter_table("sop_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_sop_runs_user_sop")
        for column in (
            "phase",
            "sop_id",
            "user_id",
            "updated_at",
            "created_at",
        ):
            batch_op.drop_index(batch_op.f(f"ix_sop_runs_{column}"))
    op.drop_table("sop_runs")

    with op.batch_alter_table("sops", schema=None) as batch_op:
        for column in ("user_id", "updated_at", "created_at"):
            batch_op.drop_index(batch_op.f(f"ix_sops_{column}"))
    op.drop_table("sops")
