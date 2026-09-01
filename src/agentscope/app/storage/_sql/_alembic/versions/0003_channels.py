# -*- coding: utf-8 -*-
"""Channel table.

Revision ID: 0003_channels
Revises: 0002_mcps_skills
Create Date: 2026-08-21 00:00:00.000000

The table is new — channels have only ever been stored on Redis — so
there is nothing to backfill. ``platform_bot_id`` is unique because no
two channels may drive the same platform bot; it replaces the separate
bot-id index key the Redis backend maintains.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "0003_channels"
down_revision: Union[str, None] = "0002_mcps_skills"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_TIMESTAMP = sa.DateTime().with_variant(
    mysql.DATETIME(fsp=6),
    "mysql",
    "mariadb",
)


def upgrade() -> None:
    """Create the ``channels`` table."""
    op.create_table(
        "channels",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("platform_bot_id", sa.String(length=255), nullable=False),
        sa.Column("id", sa.String(length=255), nullable=False),
        # Microseconds, because ``updated_at`` is the channel's
        # configuration version and MySQL's DATETIME would round
        # two edits in one second to the same value.
        sa.Column("created_at", VERSION_TIMESTAMP, nullable=False),
        sa.Column("updated_at", VERSION_TIMESTAMP, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_bot_id", name="uq_channels_bot"),
    )
    with op.batch_alter_table("channels", schema=None) as batch_op:
        for column in ("created_at", "updated_at", "user_id"):
            batch_op.create_index(
                batch_op.f(f"ix_channels_{column}"),
                [column],
                unique=False,
            )


def downgrade() -> None:
    """Drop the ``channels`` table."""
    with op.batch_alter_table("channels", schema=None) as batch_op:
        for column in ("user_id", "updated_at", "created_at"):
            batch_op.drop_index(batch_op.f(f"ix_channels_{column}"))
    op.drop_table("channels")
