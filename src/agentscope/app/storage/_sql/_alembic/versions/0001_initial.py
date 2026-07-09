# -*- coding: utf-8 -*-
"""Initial schema — creates every table defined in ``_sql/_tables.py``.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-09 12:00:00.000000

The initial revision mirrors :func:`_Base.metadata.create_all`
verbatim so a fresh database ends up with exactly the schema the
declarative tables describe. Future schema changes should be added
as new revisions (``alembic revision --autogenerate -m "..."``).
"""
from typing import Sequence, Union

from alembic import op

from agentscope.app.storage._sql._tables import _Base


# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create every table declared on :data:`_Base.metadata`.

    Delegates to :meth:`MetaData.create_all` so the revision stays in
    lockstep with the declarative definitions — additions to
    ``_tables.py`` land in this initial migration when it runs against
    an empty database, and diverge into later revisions once tables
    already exist (that is exactly the flow autogenerate assumes).
    """
    bind = op.get_bind()
    _Base.metadata.create_all(bind)


def downgrade() -> None:
    """Drop every table declared on :data:`_Base.metadata`."""
    bind = op.get_bind()
    _Base.metadata.drop_all(bind)
