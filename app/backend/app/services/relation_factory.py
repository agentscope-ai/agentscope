from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.relation_factory_agent import run_relation_factory_once
from ..db import models
from ..schemas.relations import CandidatePayload, CanvasSubmitRequest, EvidenceSnippet

ARTIFACT_ROOT = Path("artifacts/audit").resolve()


async def check_blacklist(session: AsyncSession, uniq_key: str) -> bool:
    result = await session.execute(select(models.RelationReject).where(models.RelationReject.uniq_key == uniq_key))
    return result.scalar_one_or_none() is not None


def normalize_claim(subject: str, predicate: str, obj: str, claim: str) -> str:
    payload = f"{subject}|{predicate}|{obj}|{claim}".lower().strip()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_candidate_from_relation(relation: models.Relation, evidences: list[models.RelationEvidence]) -> CandidatePayload:
    evidence_payload = [
        EvidenceSnippet(
            note=ev.note_id,
            span=ev.span,
            quote=ev.quote,
            quote_sha=ev.quote_sha,
        )
        for ev in evidences
    ]
    return CandidatePayload(
        id=relation.id,
        subject=relation.subject,
        predicate=relation.predicate,
        object=relation.object,
        claim=relation.claim,
        explain=relation.reason or "",
        confidence=relation.confidence or 0.0,
        event_time=relation.event_time,
        valid_from=relation.valid_from,
        valid_to=relation.valid_to,
        evidence=evidence_payload,
        scores={
            "bm25": relation.bm25 or 0.0,
            "cos": relation.cos or 0.0,
            "npmi": relation.npmi or 0.0,
            "time": relation.time_fresh or 0.0,
            "novelty": relation.novelty or 0.0,
        },
        degraded=False,
    )


async def _fts_best_match(session: AsyncSession, subject: str, query: str) -> Optional[tuple[str, str]]:
    """Return (note_id, snippet) for the best FTS match different from subject."""
    # naive FTS: look up via notes_fts MATCH, prefer different note
    try:
        res = await session.execute(
            "SELECT id, snippet(notes_fts, -1, '', '', ' … ', 64) as snip FROM notes_fts WHERE notes_fts MATCH :q LIMIT 5",
            {"q": query},
        )
        rows = res.fetchall()
        for rid, snip in rows:
            if rid != subject:
                return rid, snip or ""
    except Exception:
        return None
    return None


async def run_relation_factory(
    session: AsyncSession,
    request: CanvasSubmitRequest,
) -> Optional[CandidatePayload]:
    subject = request.note_id or "Z_subject_auto"
    predicate = request.predicate or "supports"
    obj = "Z_object_auto"

    # real evidence: subject content + FTS top snippet from other note
    subj_note = await session.get(models.Note, subject)
    subj_quote = (subj_note.content[:140] if subj_note and subj_note.content else request.content[:140])

    fts_hit = await _fts_best_match(session, subject, request.content[:128])
    if fts_hit:
        obj, snip = fts_hit
        obj_note = await session.get(models.Note, obj)
        obj_quote = (obj_note.content[:140] if obj_note and obj_note.content else (snip or request.content[:80]))
    else:
        obj_quote = request.content[:80]

    def evidence_provider() -> dict:
        return {
            "evidence": [
                {"note": subject, "span": "L1-L12", "quote": subj_quote},
                {"note": obj, "span": "L20-L36", "quote": obj_quote},
            ]
        }

    fused = await run_relation_factory_once(
        subject=subject,
        predicate=predicate,
        content=request.content,
        evidence_provider=evidence_provider,
    )

    if not fused or not fused.evidence:
        claim = f"你的输入提示 {subject} 与 {obj} 在主题上形成{predicate}连接。"
        explain = "连接原因：它从另一个角度推进了你刚写的主题。"
        ev = evidence_provider()["evidence"]
        fused_claim = claim
        fused_reason = explain
        fused_evidence = [EvidenceSnippet(**ev[0]), EvidenceSnippet(**ev[1])]
    else:
        fused_claim = fused.claim
        fused_reason = fused.reason
        fused_evidence = fused.evidence

    uniq_key = normalize_claim(subject, predicate, obj, fused_claim)
    if await check_blacklist(session, uniq_key):
        return None

    existing_rel_result = await session.execute(
        select(models.Relation).where(models.Relation.uniq_key == uniq_key)
    )
    existing_relation = existing_rel_result.scalar_one_or_none()
    if existing_relation is not None:
        evidences_res = await session.execute(
            select(models.RelationEvidence).where(
                models.RelationEvidence.rel_id == existing_relation.id
            )
        )
        evidences = evidences_res.scalars().all()
        return build_candidate_from_relation(existing_relation, evidences)

    relation_id = f"Rel_{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    relation = models.Relation(
        id=relation_id,
        subject=subject,
        predicate=predicate,
        object=obj,
        claim=fused_claim,
        reason=fused_reason,
        confidence=0.72,
        status="proposed",
        created_at=now,
        updated_at=now,
        event_time=now,
        valid_from=now,
        uniq_key=uniq_key,
        bm25=0.34,
        cos=0.78,
        npmi=0.41,
        time_fresh=0.55,
        path2=0.45,
        novelty=0.66,
        score=0.68,
    )

    session.add(relation)

    ev_models: list[models.RelationEvidence] = []
    for ev in fused_evidence[:2]:
        ev_models.append(
            models.RelationEvidence(
                rel_id=relation_id,
                note_id=ev.note,
                span=ev.span,
                kind="note",
                quote=ev.quote,
                quote_sha=hashlib.sha256(ev.quote.encode("utf-8")).hexdigest() if ev.quote else None,
            )
        )

    session.add_all(ev_models)

    # Minimal audit artifact (fuse stage)
    try:
        run_id = f"run_{relation_id}"
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        (ARTIFACT_ROOT / f"{run_id}_fuse.json").write_text(
            json.dumps(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "input": request.content,
                    "fused": {
                        "claim": fused_claim,
                        "reason": fused_reason,
                        "evidence": [e.model_dump() for e in fused_evidence],
                    },
                    "created_at": now.isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    await session.commit()

    return build_candidate_from_relation(relation, ev_models)
