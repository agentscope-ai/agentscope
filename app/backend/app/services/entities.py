from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models

STOPWORDS = set("the of and to in a is are be for with on at by it this that an as from or not we you i he she they".split())


def _tokenize(text: str) -> list[str]:
    import re

    return [t for t in re.split(r"[^\w]+", (text or "").lower()) if t and t not in STOPWORDS and len(t) > 1]


async def rebuild_entities(session: AsyncSession) -> dict:
    # load all notes
    res = await session.execute(select(models.Note).where(models.Note.deleted == 0))
    notes = res.scalars().all()

    # clear tables
    await session.execute(delete(models.NoteEntity))
    await session.execute(delete(models.Entity))

    df: Dict[str, int] = defaultdict(int)
    tf_map: Dict[str, Counter] = {}

    for n in notes:
        tokens = _tokenize((n.title or "") + "\n" + (n.content or ""))
        tf = Counter(tokens)
        tf_map[n.id] = tf
        for t in tf.keys():
            df[t] += 1

    # insert entities
    for name, d in df.items():
        ent_id = f"E_{hash(name) & 0xffffffff:x}"
        session.add(models.Entity(id=ent_id, name=name, type=None, df=d))

    # map name->id
    await session.commit()
    res = await session.execute(select(models.Entity))
    ents = res.scalars().all()
    id_map = {e.name: e.id for e in ents}

    cnt = 0
    for note_id, tf in tf_map.items():
        for name, c in tf.items():
            ent_id = id_map.get(name)
            if not ent_id:
                continue
            session.add(models.NoteEntity(note_id=note_id, entity_id=ent_id, cnt=int(c)))
            cnt += 1
    await session.commit()
    return {"entities": len(ents), "note_entities": cnt}
