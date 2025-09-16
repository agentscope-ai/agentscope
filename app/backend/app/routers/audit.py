from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session
from ..schemas.relations import AuditArtifact, AuditResponse


router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/{relation_id}", response_model=AuditResponse)
async def get_audit(
    relation_id: str,
    session: AsyncSession = Depends(get_session),
) -> AuditResponse:
    relation = await session.get(models.Relation, relation_id)
    if relation is None:
        raise HTTPException(status_code=404, detail="relation not found")

    run_id = f"run_{relation_id}"
    artifacts = []
    import os
    from pathlib import Path

    fuse_path = Path("artifacts/audit") / f"{run_id}_fuse.json"
    if fuse_path.exists():
        artifacts.append(AuditArtifact(step="fuse", path=str(fuse_path)))

    return AuditResponse(
        relation_id=relation_id,
        run_id=run_id,
        status=relation.status,
        created_at=relation.created_at or datetime.now(timezone.utc),
        prompt_hash=None,
        input_hash=None,
        cost_cents=None,
        artifacts=artifacts,
        notes=None if artifacts else "模拟数据，正式实现会返回真实审计信息。",
    )
