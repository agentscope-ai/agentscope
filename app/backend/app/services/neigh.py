from __future__ import annotations

from typing import Dict, List, Tuple

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .emb import cosine
from ..db import models


async def rebuild_neighbors(session: AsyncSession, k: int = 30) -> dict:
    # load all embeddings
    res = await session.execute(select(models.Embedding))
    embs = res.scalars().all()
    emb_map: Dict[str, models.Embedding] = {e.id: e for e in embs}

    # clear table
    await session.execute(delete(models.Neighbor))

    ids = list(emb_map.keys())
    for i, a in enumerate(ids):
        sims: List[Tuple[str, float]] = []
        va = emb_map[a].vec
        for b in ids:
            if b == a:
                continue
            vb = emb_map[b].vec
            sims.append((b, cosine(va, vb)))
        sims.sort(key=lambda x: -x[1])
        for rank, (nbr, score) in enumerate(sims[:k], start=1):
            session.add(models.Neighbor(note_id=a, nbr_id=nbr, rank=rank, score=float(score)))
    await session.commit()
    return {"neighbors": len(ids)}
