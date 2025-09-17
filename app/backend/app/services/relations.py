"""Utilities for managing relation lifecycle operations."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# Default polling interval for finalizer loop (in seconds).
_FINALIZER_INTERVAL = 1.0
# Maximum number of relations to finalize per iteration.
_FINALIZER_BATCH_SIZE = 100


async def _finalize_relations(session: AsyncSession, relations: Sequence[models.Relation]) -> None:
    """Finalize the provided relations and produce audit history records."""
    now = datetime.now(timezone.utc)

    for idx, relation in enumerate(relations):
        relation.status = "verified"
        relation.undo_expires_at = None
        relation.updated_at = now

        run_id = f"finalize:{relation.id}:{int(now.timestamp() * 1000)}:{idx}"
        session.add(models.AuditRun(run_id=run_id, kind="finalize"))
        session.add(
            models.RelationAudit(
                rel_id=relation.id,
                run_id=run_id,
                decision="verify",
                action_source="review",
            )
        )

    await session.flush()


async def finalize_pending_loop(
    *, interval: float = _FINALIZER_INTERVAL, batch_size: int = _FINALIZER_BATCH_SIZE
) -> None:
    """Periodically promote pending relations once their undo window expires."""
    while True:
        try:
            async with SessionLocal() as session:
                now = datetime.now(timezone.utc)
                stmt = (
                    select(models.Relation)
                    .where(
                        models.Relation.status == "pending",
                        models.Relation.undo_expires_at.is_not(None),
                        models.Relation.undo_expires_at <= now,
                    )
                    .limit(batch_size)
                )
                result = await session.execute(stmt)
                relations = result.scalars().all()

                if relations:
                    await _finalize_relations(session, relations)
                    await session.commit()
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            raise
        except Exception:  # pragma: no cover - log unexpected failures
            logger.exception("finalize_pending_loop encountered an error")
        await asyncio.sleep(interval)
