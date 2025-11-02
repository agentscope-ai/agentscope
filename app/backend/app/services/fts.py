from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


NOTES_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
  id, title, content, tags, tokenize='unicode61'
);
"""

REFRESH_FTS_DELETE = """
DELETE FROM notes_fts;
"""

REFRESH_FTS_INSERT = """
INSERT INTO notes_fts(id,title,content,tags)
SELECT id,title,content,tags FROM notes WHERE deleted=0;
"""


async def ensure_fts(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(NOTES_FTS_DDL))


async def rebuild_fts(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(REFRESH_FTS_DELETE))
        await conn.execute(text(REFRESH_FTS_INSERT))
