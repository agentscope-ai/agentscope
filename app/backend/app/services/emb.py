from __future__ import annotations

import math
from array import array
from collections import Counter
from typing import Iterable, Tuple

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models

DIM = 256
MODEL = "hash-embed-v0"


def _tokenize(text: str) -> list[str]:
    # very simple tokenizer split on non-alnum
    import re

    return [t for t in re.split(r"[^\w]+", (text or "").lower()) if t]


def embed_text(text: str) -> bytes:
    vec = [0.0] * DIM
    for tok in _tokenize(text):
        h = hash(tok) % DIM
        vec[h] += 1.0
    # l2 normalize
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    vec = [v / norm for v in vec]
    arr = array("f", vec)
    return arr.tobytes()


def cosine(a: bytes, b: bytes) -> float:
    arr_a = array("f")
    arr_a.frombytes(a)
    arr_b = array("f")
    arr_b.frombytes(b)
    s = 0.0
    for x, y in zip(arr_a, arr_b):
        s += x * y
    return float(s)


async def rebuild_embeddings(session: AsyncSession) -> dict:
    q = await session.execute(models.Note.__table__.select().where(models.Note.deleted == 0))
    rows = q.fetchall()

    # truncate and rebuild for simplicity
    await session.execute(delete(models.Embedding))

    count = 0
    for r in rows:
        note = models.Note(**dict(r)) if not isinstance(r, models.Note) else r
        vec = embed_text(note.content or (note.title or ""))
        session.add(
            models.Embedding(id=note.id, vec=vec, dim=DIM, model=MODEL)
        )
        count += 1
    await session.commit()
    return {"embeddings": count}
