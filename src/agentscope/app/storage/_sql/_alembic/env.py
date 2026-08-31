# -*- coding: utf-8 -*-
"""Alembic environment for :class:`AsyncSQLAlchemyStorage` schema migrations.

Runs migrations in async mode against a SQLAlchemy URL. The URL is
picked up (in order of precedence) from:

1. the ``sqlalchemy.url`` key of the Alembic config (usually set via
   ``alembic.ini`` or ``-x url=...``);
2. the ``AGENTSCOPE_SQL_URL`` environment variable — convenient in
   dev / CI when there is no ``alembic.ini`` around.

The target metadata is :data:`_Base.metadata`, so every model added
to :mod:`agentscope.app.storage._sql._tables` is picked up by
``alembic revision --autogenerate`` without further wiring.
"""
from __future__ import with_statement

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from agentscope.app.storage._sql._tables import _Base

# ---------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------
config = context.config

url_from_env = os.getenv("AGENTSCOPE_SQL_URL")
if url_from_env and not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", url_from_env)

target_metadata = _Base.metadata


# ---------------------------------------------------------------------
# Offline mode — emit SQL without a connection.
# ---------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations using URL-only context (no DBAPI connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=(url or "").startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------
# Online mode — connect and run.
# ---------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    """Bind Alembic to *connection* and run every pending revision."""
    is_sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Create an :class:`AsyncEngine` and dispatch to
    :func:`do_run_migrations`.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
