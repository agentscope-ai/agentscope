from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session
from ..schemas.relations import (
    DecideRequest,
    DecideResponse,
    RelationDecision,
    SuggestResponse,
    SwapRequest,
)
from ..services.relation_factory import build_candidate_from_relation


router = APIRouter(prefix="/relations", tags=["relations"])


async def load_relation_candidate(session: AsyncSession, relation: models.Relation) -> SuggestResponse:
    evidences_result = await session.execute(
        select(models.RelationEvidence).where(models.RelationEvidence.rel_id == relation.id)
    )
    evidences = evidences_result.scalars().all()
    candidate = build_candidate_from_relation(relation, evidences)
    return SuggestResponse(candidate=candidate, degraded=False)


@router.get("/suggest", response_model=SuggestResponse)
async def suggest_relation(
    subject: str,
    as_of: Optional[datetime] = None,
    session: AsyncSession = Depends(get_session),
) -> SuggestResponse:
    if not subject:
        raise HTTPException(status_code=400, detail="subject must not be empty")

    stmt = (
        select(models.Relation)
        .where(models.Relation.subject == subject, models.Relation.status == "proposed")
        .order_by(models.Relation.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    relation = result.scalar_one_or_none()
    if relation is None:
        return SuggestResponse(candidate=None, degraded=False)

    return await load_relation_candidate(session, relation)


async def verify_relation(session: AsyncSession, decision: RelationDecision) -> Optional[datetime]:
    relation = await session.get(models.Relation, decision.id)
    if relation is None:
        raise HTTPException(status_code=404, detail=f"relation {decision.id} not found")

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=3)

    relation.status = "pending"
    relation.undo_expires_at = deadline
    relation.updated_at = now

    await session.commit()
    return deadline


async def reject_relation(session: AsyncSession, decision: RelationDecision) -> None:
    relation = await session.get(models.Relation, decision.id)
    if relation is None:
        raise HTTPException(status_code=404, detail=f"relation {decision.id} not found")

    relation.status = "rejected"
    relation.undo_expires_at = None
    relation.updated_at = datetime.now(timezone.utc)

    if relation.uniq_key:
        existing = await session.execute(
            select(models.RelationReject).where(models.RelationReject.uniq_key == relation.uniq_key)
        )
        if existing.scalar_one_or_none() is None:
            session.add(
                models.RelationReject(
                    uniq_key=relation.uniq_key,
                    reason=decision.reason or "rejected",
                    decided_by="reviewer",
                )
            )

    await session.commit()


async def undo_relation(session: AsyncSession, decision: RelationDecision) -> None:
    relation = await session.get(models.Relation, decision.id)
    if relation is None:
        raise HTTPException(status_code=404, detail=f"relation {decision.id} not found")

    now = datetime.now(timezone.utc)
    if relation.undo_expires_at is None or relation.undo_expires_at <= now:
        raise HTTPException(status_code=400, detail="undo window expired")

    relation.status = "proposed"
    relation.undo_expires_at = None
    relation.updated_at = now
    await session.commit()


@router.post("/decide", response_model=DecideResponse)
async def decide_relations(
    payload: DecideRequest,
    session: AsyncSession = Depends(get_session),
) -> DecideResponse:
    failed: list[str] = []
    undo_deadline: Optional[datetime] = None

    for op in payload.ops:
        try:
            if op.action == "verify":
                undo_deadline = await verify_relation(session, op)
            elif op.action == "reject":
                await reject_relation(session, op)
            elif op.action == "undo":
                await undo_relation(session, op)
            else:
                failed.append(op.id)
        except HTTPException:
            failed.append(op.id)

    return DecideResponse(ok=len(payload.ops) - len(failed), failed=failed, undo_expires_at=undo_deadline)


@router.post("/swap", response_model=SuggestResponse)
async def swap_relation(
    payload: SwapRequest,
    session: AsyncSession = Depends(get_session),
) -> SuggestResponse:
    relation = await session.get(models.Relation, payload.id)
    if relation is None:
        raise HTTPException(status_code=404, detail="relation not found")

    stmt = (
        select(models.Relation)
        .where(
            models.Relation.subject == payload.subject,
            models.Relation.status == "proposed",
            models.Relation.id != payload.id,
        )
        .order_by(models.Relation.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    next_relation = result.scalar_one_or_none()
    if next_relation is None:
        return await load_relation_candidate(session, relation)

    return await load_relation_candidate(session, next_relation)
