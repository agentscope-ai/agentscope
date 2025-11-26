from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session, engine
from ..services.fts import ensure_fts, rebuild_fts

router = APIRouter(prefix="/notes", tags=["notes"])


class NoteIn(BaseModel):
    title: str
    content: str
    summary: Optional[str] = ""
    tags: list[str] = Field(default_factory=list)


class NoteOut(BaseModel):
    id: str
    title: str
    summary: str
    tags: list[str]
    content: str
    created_at: datetime
    updated_at: datetime


def _zid(seed: str) -> str:
    import hashlib

    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"Z_{h}"


@router.on_event("startup")
async def notes_startup() -> None:
    # Ensure tables and FTS exist
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    await ensure_fts(engine)


@router.post("", response_model=NoteOut)
async def create_note(payload: NoteIn, session: AsyncSession = Depends(get_session)) -> NoteOut:
    note_id = _zid(payload.title + payload.content[:128])
    now = datetime.now(timezone.utc)
    tags = ",".join(payload.tags)

    existing = await session.get(models.Note, note_id)
    if existing:
        # Update existing note
        existing.title = payload.title
        existing.summary = payload.summary or ""
        existing.tags = tags
        existing.content = payload.content
        existing.updated_at = now
        await session.commit()
        await rebuild_fts(engine)
        return NoteOut(
            id=existing.id,
            title=existing.title,
            summary=existing.summary or "",
            tags=existing.tags.split(",") if existing.tags else [],
            content=existing.content or "",
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    note = models.Note(
        id=note_id,
        title=payload.title,
        summary=payload.summary or "",
        tags=tags,
        content=payload.content,
        created_at=now,
        updated_at=now,
        deleted=0,
    )
    session.add(note)
    await session.commit()
    await rebuild_fts(engine)

    return NoteOut(
        id=note.id,
        title=note.title,
        summary=note.summary or "",
        tags=note.tags.split(",") if note.tags else [],
        content=note.content or "",
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.get("/{note_id}", response_model=NoteOut)
async def get_note(note_id: str, session: AsyncSession = Depends(get_session)) -> NoteOut:
    note = await session.get(models.Note, note_id)
    if not note or note.deleted:
        raise HTTPException(status_code=404, detail="note not found")
    return NoteOut(
        id=note.id,
        title=note.title,
        summary=note.summary or "",
        tags=note.tags.split(",") if note.tags else [],
        content=note.content or "",
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


class ReindexReq(BaseModel):
    fts: bool = True
    emb: bool = False
    neigh: bool = False
    entities: bool = False
    k: int = 30


@router.post("/reindex")
async def reindex(req: ReindexReq, session: AsyncSession = Depends(get_session)) -> dict:
    out = {}
    if req.fts:
        await rebuild_fts(engine)
        out["fts"] = True
    if req.emb:
        from ..services.emb import rebuild_embeddings
        out.update(await rebuild_embeddings(session))
    if req.entities:
        from ..services.entities import rebuild_entities
        out.update(await rebuild_entities(session))
    if req.neigh:
        from ..services.neigh import rebuild_neighbors
        out.update(await rebuild_neighbors(session, k=req.k))
    return {"ok": True, **out}
