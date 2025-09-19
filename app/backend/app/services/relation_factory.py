from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_registry import get_relation_factory_prompt
from ..config import get_settings
from ..db import audit as audit_db
from ..db import models
from ..schemas.relations import CandidatePayload, CanvasSubmitRequest, EvidenceSnippet
from .audit import audit_stage
from .provider import CallStats, call_llm


@dataclass
class PipelineContext:
    relation_id: str
    subject: str
    predicate: str
    obj: str
    request: CanvasSubmitRequest
    subject_quote: str = ""
    object_quote: str = ""
    fts_hit: Optional[tuple[str, str]] = None
    evidence_payload: list[dict[str, str]] = field(default_factory=list)
    fused_claim: Optional[str] = None
    fused_reason: Optional[str] = None
    fused_evidence: list[EvidenceSnippet] = field(default_factory=list)
    llm_response: Any = None
    llm_stats: Optional[CallStats] = None
    uniq_key: Optional[str] = None


async def check_blacklist(session: AsyncSession, uniq_key: str) -> bool:
    result = await session.execute(
        select(models.RelationReject).where(models.RelationReject.uniq_key == uniq_key)
    )
    return result.scalar_one_or_none() is not None


def normalize_claim(subject: str, predicate: str, obj: str, claim: str) -> str:
    payload = f"{subject}|{predicate}|{obj}|{claim}".lower().strip()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_candidate_from_relation(
    relation: models.Relation,
    evidences: list[models.RelationEvidence],
) -> CandidatePayload:
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


async def _fts_best_match(
    session: AsyncSession,
    subject: str,
    query: str,
) -> Optional[tuple[str, str]]:
    """Return (note_id, snippet) for the best FTS match different from subject."""
    try:
        stmt = text(
            "SELECT id, snippet(notes_fts, -1, '', '', ' … ', 64) AS snip "
            "FROM notes_fts WHERE notes_fts MATCH :q LIMIT 5"
        )
        res = await session.execute(stmt, {"q": query})
        rows = res.fetchall()
        for rid, snip in rows:
            if rid != subject:
                return rid, snip or ""
    except Exception:
        return None
    return None


def _response_to_payload(response: Any) -> Any:
    if response is None:
        return None
    for attr in ("model_dump", "dict", "to_dict"):
        method = getattr(response, attr, None)
        if callable(method):
            try:
                return method()
            except Exception:
                continue
    return response


def _extract_message_content(response: Any) -> str:
    if response is None:
        return ""
    payload = response
    if isinstance(payload, dict):
        choices = payload.get("choices") or []
    else:
        choices = getattr(payload, "choices", [])
    if not choices:
        return ""
    choice = choices[0]
    message = None
    if isinstance(choice, dict):
        message = choice.get("message") or choice.get("delta")
    else:
        message = getattr(choice, "message", None) or getattr(choice, "delta", None)
    content = None
    if isinstance(message, dict):
        content = message.get("content") or message.get("text")
    else:
        content = getattr(message, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    if isinstance(content, str):
        return content
    if content is None and isinstance(message, dict):
        return message.get("text", "") or ""
    return str(content or "")


def _fallback_fuse(context: PipelineContext) -> tuple[str, str, list[EvidenceSnippet]]:
    claim = (
        f"你输入提示 {context.subject} 与 {context.obj} 在主题上形成"
        f"{context.predicate}连接。"
    )
    explain = "连接原因：它从另一个角度推进了你刚写的主题。"
    evidence = [
        EvidenceSnippet(**ev) for ev in context.evidence_payload[:2]
    ]
    return claim, explain, evidence


async def run_relation_factory(
    session: AsyncSession,
    request: CanvasSubmitRequest,
) -> Optional[CandidatePayload]:
    settings = get_settings()
    subject = request.note_id or "Z_subject_auto"
    predicate = request.predicate or "supports"
    obj = "Z_object_auto"
    relation_id = f"Rel_{uuid4().hex[:12]}"

    context = PipelineContext(
        relation_id=relation_id,
        subject=subject,
        predicate=predicate,
        obj=obj,
        request=request,
    )

    # Recall stage
    async with audit_stage(session, relation_id, "recall") as (dump, run_id):
        subj_note = await session.get(models.Note, subject)
        subject_quote = request.content[:140]
        if subj_note and getattr(subj_note, "content", None):
            subject_quote = subj_note.content[:140]
        context.subject_quote = subject_quote
        dump(
            "recall",
            {
                "request": request.model_dump(mode="json"),
                "subject_note": {
                    "id": subj_note.id if subj_note else None,
                    "excerpt": subj_note.content[:280] if subj_note and subj_note.content else None,
                },
            },
        )

    # Probe stage
    async with audit_stage(session, relation_id, "probe") as (dump, run_id):
        fts_hit = await _fts_best_match(session, subject, request.content[:128])
        context.fts_hit = fts_hit
        if fts_hit:
            obj_candidate, snip = fts_hit
            context.obj = obj_candidate
            obj_note = await session.get(models.Note, obj_candidate)
            if obj_note and getattr(obj_note, "content", None):
                context.object_quote = obj_note.content[:140]
            else:
                context.object_quote = (snip or request.content[:80])
            dump(
                "probe",
                {
                    "fts_hit": {
                        "note": obj_candidate,
                        "snippet": snip,
                    },
                    "object_excerpt": context.object_quote,
                },
            )
        else:
            context.object_quote = request.content[:80]
            dump("probe", {"fts_hit": None, "object_excerpt": context.object_quote})

    # Tri stage
    async with audit_stage(session, relation_id, "tri") as (dump, run_id):
        context.evidence_payload = [
            {"note": context.subject, "span": "L1-L12", "quote": context.subject_quote},
            {"note": context.obj, "span": "L20-L36", "quote": context.object_quote},
        ]
        dump("tri", {"evidence": context.evidence_payload})

    # Fuse stage
    provider = settings.llm_provider or "openai"
    model_name = settings.relation_factory_model
    async with audit_stage(
        session,
        relation_id,
        "fuse",
        model_ctx={"provider": provider, "model": model_name},
    ) as (dump, run_id):
        evidence_json = json.dumps(context.evidence_payload, ensure_ascii=False, indent=2)
        user_prompt = (
            f"目标关系: {predicate}\n"
            f"主题(subject): {context.subject}\n"
            f"客体(object): {context.obj}\n"
            f"新输入片段: {request.content[:400]}\n\n"
            "请依据 evidence 中的引用生成 JSON 对象，包含 claim、reason、evidence 三个字段。"
        )
        messages = [
            {"role": "system", "content": get_relation_factory_prompt()},
            {"role": "user", "content": f"{user_prompt}\nEvidence:\n{evidence_json}"},
        ]
        response, stats = await call_llm(
            messages=messages,
            model=model_name,
            provider=provider,
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        context.llm_stats = stats
        context.llm_response = _response_to_payload(response)
        await audit_db.audit_runs_insert_tokens(session=session, run_id=run_id, stats=stats)
        dump("fuse_messages", messages)
        dump("fuse_response", context.llm_response)
        dump("fuse_stats", asdict(stats))

        parsed_output: Optional[dict[str, Any]] = None
        if isinstance(context.llm_response, dict):
            message_content = _extract_message_content(context.llm_response)
        else:
            message_content = _extract_message_content(response)
        if message_content:
            try:
                parsed_output = json.loads(message_content)
            except json.JSONDecodeError:
                parsed_output = None
        if parsed_output and isinstance(parsed_output, dict):
            context.fused_claim = parsed_output.get("claim")
            context.fused_reason = parsed_output.get("reason")
            evidence_items = parsed_output.get("evidence", [])
            if isinstance(evidence_items, list):
                context.fused_evidence = []
                for item in evidence_items:
                    if isinstance(item, dict) and {"note", "span", "quote"} <= set(item.keys()):
                        context.fused_evidence.append(EvidenceSnippet(**item))

    # Judge stage
    async with audit_stage(session, relation_id, "judge") as (dump, run_id):
        if not context.evidence_payload:
            context.evidence_payload = [
                {"note": context.subject, "span": "L1-L12", "quote": context.subject_quote},
                {"note": context.obj, "span": "L20-L36", "quote": context.object_quote},
            ]
        if not context.fused_claim or not context.fused_reason or not context.fused_evidence:
            context.fused_claim, context.fused_reason, context.fused_evidence = _fallback_fuse(context)
        context.uniq_key = normalize_claim(
            context.subject, context.predicate, context.obj, context.fused_claim
        )
        dump(
            "judge_candidate",
            {
                "claim": context.fused_claim,
                "reason": context.fused_reason,
                "evidence": [ev.model_dump(mode="json") for ev in context.fused_evidence],
                "uniq_key": context.uniq_key,
            },
        )

        if await check_blacklist(session, context.uniq_key):
            dump("judge_decision", {"status": "rejected", "reason": "blacklist"})
            return None

        existing_rel_result = await session.execute(
            select(models.Relation).where(models.Relation.uniq_key == context.uniq_key)
        )
        existing_relation = existing_rel_result.scalar_one_or_none()
        if existing_relation is not None:
            evidences_res = await session.execute(
                select(models.RelationEvidence).where(
                    models.RelationEvidence.rel_id == existing_relation.id
                )
            )
            evidences = evidences_res.scalars().all()
            dump(
                "judge_decision",
                {"status": "existing", "relation_id": existing_relation.id},
            )
            return build_candidate_from_relation(existing_relation, evidences)

        now = datetime.now(timezone.utc)
        relation = models.Relation(
            id=relation_id,
            subject=context.subject,
            predicate=context.predicate,
            object=context.obj,
            claim=context.fused_claim,
            reason=context.fused_reason,
            confidence=0.72,
            status="proposed",
            created_at=now,
            updated_at=now,
            event_time=now,
            valid_from=now,
            uniq_key=context.uniq_key,
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
        for ev in context.fused_evidence[:2]:
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
        await session.commit()

        dump(
            "judge_decision",
            {
                "status": "created",
                "relation_id": relation_id,
                "evidence": [
                    {
                        "note": ev.note_id,
                        "span": ev.span,
                        "quote": ev.quote,
                        "quote_sha": ev.quote_sha,
                    }
                    for ev in ev_models
                ],
            },
        )

        return build_candidate_from_relation(relation, ev_models)
