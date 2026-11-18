# -*- coding: utf-8 -*-
"""Channel-user OAuth credential table.

Revision ID: 0004_channel_user_credentials
Revises: 0003_channels
Create Date: 2026-09-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "0004_channel_user_credentials"
down_revision: Union[str, None] = "0003_channels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_TIMESTAMP = sa.DateTime().with_variant(
    mysql.DATETIME(fsp=6),
    "mysql",
    "mariadb",
)


def upgrade() -> None:
    """Create the ``channel_user_credentials`` table."""
    op.create_table(
        "channel_user_credentials",
        sa.Column("channel_id", sa.String(length=255), nullable=False),
        sa.Column("channel_user_id", sa.String(length=255), nullable=False),
        sa.Column("credentials", sa.JSON(), nullable=False),
        sa.Column("updated_at", VERSION_TIMESTAMP, nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", "channel_user_id"),
    )


def downgrade() -> None:
    """Drop the ``channel_user_credentials`` table."""
    op.drop_table("channel_user_credentials")
