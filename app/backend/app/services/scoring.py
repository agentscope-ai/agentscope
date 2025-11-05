from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models


def _normalize(values: List[float]) -> List[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi - lo < 1e-9:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo + 1e-9) for v in values]


async def compute_direct_scores(session: AsyncSession, a: str, candidates: List[str]) -> Dict[str, Dict[str, float]]:
    # BM25 via FTS
    bm: Dict[str, float] = {}
    try:
        res = await session.execute(
            "SELECT id, bm25(notes_fts) as bm FROM notes_fts WHERE id IN (%s)" % ",".join([f"'{c}'" for c in candidates])
        )
        for nid, v in res.fetchall():
            bm[nid] = float(v or 0.0)
    except Exception:
        bm = {c: 0.0 for c in candidates}

    # cosine via embeddings
    res = await session.execute(select(models.Embedding).where(models.Embedding.id.in_(candidates + [a])))
    embs = {e.id: e.vec for e in res.scalars().all()}
    va = embs.get(a)
    from ..services.emb import cosine

    cos_map = {}
    for c in candidates:
        vb = embs.get(c)
        if va and vb:
            cos_map[c] = cosine(va, vb)
        else:
            cos_map[c] = 0.0

    # NPMI approximation using rare shared entities
    df_map: Dict[str, int] = {}
    res = await session.execute(select(models.Entity))
    for e in res.scalars().all():
        df_map[e.id] = e.df or 0

    def ents(note_id: str) -> List[str]:
        r = session.execute  # type: ignore
        return []

    res = await session.execute(select(models.NoteEntity).where(models.NoteEntity.note_id == a))
    ents_a = [ne.entity_id for ne in res.scalars().all()]

    npmi: Dict[str, float] = {}
    for c in candidates:
        res = await session.execute(select(models.NoteEntity).where(models.NoteEntity.note_id == c))
        ents_c = [ne.entity_id for ne in res.scalars().all()]
        shared = set(ents_a) & set(ents_c)
        score = 0.0
        for eid in shared:
            df = max(1, df_map.get(eid, 1))
            score += 1.0 / math.log2(df + 2)
        # normalize later
        npmi[c] = score

    # time freshness (using created_at)
    res = await session.execute(select(models.Note).where(models.Note.id.in_(candidates + [a])))
    note_map = {n.id: n for n in res.scalars().all()}
    from datetime import datetime

    def freshness(xid: str) -> float:
        aa = note_map.get(a)
        bb = note_map.get(xid)
        if not aa or not bb:
            return 0.5
        dt = abs((bb.created_at or aa.created_at) - (aa.created_at or bb.created_at)).days if hasattr(aa.created_at, 'days') else 0
        # exp decay with tau=30 days
        return math.exp(-float(dt) / 30.0)

    time_map = {c: freshness(c) for c in candidates}

    # Jaccard over neighbors (if available), else 0
    j2 = {c: 0.0 for c in candidates}

    # Normalize and fuse
    bm_values = [bm.get(c, 0.0) for c in candidates]
    bm_norm = _normalize(bm_values)

    npmi_values = [npmi.get(c, 0.0) for c in candidates]
    npmi_norm = _normalize(npmi_values)

    out: Dict[str, Dict[str, float]] = {}
    for idx, c in enumerate(candidates):
        fused = (
            0.2 * bm_norm[idx]
            + 0.4 * cos_map.get(c, 0.0)
            + 0.25 * npmi_norm[idx]
            + 0.1 * time_map.get(c, 0.0)
            + 0.05 * j2.get(c, 0.0)
        )
        out[c] = {
            "bm25": bm_norm[idx],
            "cos": cos_map.get(c, 0.0),
            "npmi": npmi_norm[idx],
            "time": time_map.get(c, 0.0),
            "j2": j2.get(c, 0.0),
            "S": fused,
        }
    return out
