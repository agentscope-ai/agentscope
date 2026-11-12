# -*- coding: utf-8 -*-
"""Index session lists by owner, agent and stable newest-first order."""
from alembic import op


revision = "0006_session_list_index"
down_revision = "0005_voice_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Support ordered session pages without sorting their payloads."""
    op.create_index(
        "ix_sessions_user_agent_created_id",
        "sessions",
        ["user_id", "agent_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Remove only the session list index."""
    op.drop_index("ix_sessions_user_agent_created_id", "sessions")
