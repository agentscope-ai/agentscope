from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import models
from ..db.session import get_session, engine
from ..schemas.relations import (
    CanvasSubmitRequest,
    CanvasSubmitResponse,
)
from ..services.relation_factory import run_relation_factory


router = APIRouter(prefix="/canvas", tags=["canvas"])


@router.on_event("startup")
async def on_startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)


@router.post("/submit", response_model=CanvasSubmitResponse)
async def submit_canvas(
    request: CanvasSubmitRequest,
    session: AsyncSession = Depends(get_session),
) -> CanvasSubmitResponse:
    settings = get_settings()

    candidate = await run_relation_factory(session, request)

    if candidate is None:
        raise HTTPException(status_code=409, detail="Candidate blocked by blacklist")

    budget_used = request.budget_cents if request.budget_cents is not None else settings.budget_cents_default

    return CanvasSubmitResponse(
        run_id=f"run_{candidate.id}",
        candidate=candidate,
        guidance=None,
        budget_used=budget_used,
        degraded=False,
    )
