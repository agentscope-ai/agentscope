from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session
from ..schemas.relations import ReviewCard, ReviewDailyResponse
from ..services.relation_factory import build_candidate_from_relation


router = APIRouter(prefix="/review", tags=["review"])


@router.get("/daily", response_model=ReviewDailyResponse)
async def get_daily_review(
    limit: Optional[int] = 5,
    session: AsyncSession = Depends(get_session),
) -> ReviewDailyResponse:
    stmt = (
        select(models.Relation)
        .where(models.Relation.status == "proposed")
        .order_by(models.Relation.created_at.asc())
        .limit(limit or 5)
    )
    result = await session.execute(stmt)
    relations = result.scalars().all()

    cards: list[ReviewCard] = []
    total = len(relations)
    for idx, relation in enumerate(relations, start=1):
        evidences_result = await session.execute(
            select(models.RelationEvidence).where(models.RelationEvidence.rel_id == relation.id)
        )
        evidences = evidences_result.scalars().all()
        candidate = build_candidate_from_relation(relation, evidences)
        cards.append(ReviewCard(position=idx, total=total, candidate=candidate))

    return ReviewDailyResponse(cards=cards)
