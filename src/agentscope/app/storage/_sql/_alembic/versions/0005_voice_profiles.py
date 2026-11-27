# -*- coding: utf-8 -*-
"""Voice profile table.

Revision ID: 0005_voice_profiles
Revises: 0004_sops
Create Date: 2026-10-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_voice_profiles"
down_revision: Union[str, None] = "0004_sops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create voice profiles and their timestamp and ownership indexes."""
    op.create_table(
        "voice_profiles",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("voice_profiles", schema=None) as batch_op:
        for column in ("created_at", "updated_at", "user_id"):
            batch_op.create_index(
                batch_op.f(f"ix_voice_profiles_{column}"),
                [column],
                unique=False,
            )


def downgrade() -> None:
    """Drop the voice profile table and its indexes."""
    with op.batch_alter_table("voice_profiles", schema=None) as batch_op:
        for column in ("user_id", "updated_at", "created_at"):
            batch_op.drop_index(batch_op.f(f"ix_voice_profiles_{column}"))
    op.drop_table("voice_profiles")
