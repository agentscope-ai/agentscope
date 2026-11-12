# -*- coding: utf-8 -*-
"""Merge channel-user credentials and voice-profile migrations.

Revision ID: 0006_merge_channel_credentials
Revises: 0004_channel_user_credentials, 0005_voice_profiles
"""
from typing import Sequence, Union

revision: str = "0006_merge_channel_credentials"
down_revision: tuple[str, str] = (
    "0004_channel_user_credentials",
    "0005_voice_profiles",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join the two migration branches without changing tables."""


def downgrade() -> None:
    """Restore the two migration branches without changing tables."""
