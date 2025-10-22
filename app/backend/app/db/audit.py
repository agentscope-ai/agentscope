from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import models
from .session import SessionLocal

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from ..services.provider import CallStats


@asynccontextmanager
async def _scoped_session(session: Optional[AsyncSession]) -> AsyncIterator[AsyncSession]:
    if session is not None and session.bind is not None:
        maker = async_sessionmaker(bind=session.bind, expire_on_commit=False)
        async with maker() as scoped:
            yield scoped
    else:
        async with SessionLocal() as scoped:
            yield scoped


def _set_if_exists(obj: Any, attr: str, value: Any) -> None:
    if value is None:
        return
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def _collect_token_updates(model_cls: type, stats: "CallStats") -> dict[str, Any]:
    values: dict[str, Any] = {}
    if hasattr(model_cls, "prompt_tokens"):
        values["prompt_tokens"] = stats.prompt_tokens
    if hasattr(model_cls, "completion_tokens"):
        values["completion_tokens"] = stats.completion_tokens
    if hasattr(model_cls, "total_tokens"):
        values["total_tokens"] = stats.total_tokens
    if hasattr(model_cls, "cost_cents") and stats.cost_cents is not None:
        values["cost_cents"] = stats.cost_cents
    return values


async def audit_runs_insert_start(
    *,
    session: Optional[AsyncSession],
    run_id: str,
    relation_id: str,
    stage: str,
    model_ctx: dict[str, Any],
    started_at: datetime,
) -> None:
    model_cls = getattr(models, "AuditRun2", models.AuditRun)
    async with _scoped_session(session) as scoped:
        try:
            record = model_cls(run_id=run_id)
            _set_if_exists(record, "relation_id", relation_id)
            _set_if_exists(record, "rel_id", relation_id)
            _set_if_exists(record, "stage", stage)
            _set_if_exists(record, "kind", stage)
            _set_if_exists(record, "model", model_ctx.get("model"))
            _set_if_exists(record, "provider", model_ctx.get("provider"))
            _set_if_exists(record, "metadata", model_ctx)
            _set_if_exists(record, "model_ctx", model_ctx)
            _set_if_exists(record, "started_at", started_at)
            _set_if_exists(record, "status", "running")
            await scoped.merge(record)
            await scoped.commit()
        except Exception:
            await scoped.rollback()


async def audit_runs_insert_tokens(
    *,
    session: Optional[AsyncSession],
    run_id: str,
    stats: "CallStats",
) -> None:
    model_cls = getattr(models, "AuditRun2", models.AuditRun)
    values = _collect_token_updates(model_cls, stats)
    if not values:
        return
    async with _scoped_session(session) as scoped:
        try:
            stmt = (
                update(model_cls)
                .where(model_cls.run_id == run_id)
                .values(**values)
            )
            await scoped.execute(stmt)
            await scoped.commit()
        except Exception:
            await scoped.rollback()


async def audit_runs_insert_finish(
    *,
    session: Optional[AsyncSession],
    run_id: str,
    finished_at: datetime,
    status: str,
    cost_cents: Optional[int] = None,
) -> None:
    model_cls = getattr(models, "AuditRun2", models.AuditRun)
    values: dict[str, Any] = {}
    if hasattr(model_cls, "finished_at"):
        values["finished_at"] = finished_at
    if hasattr(model_cls, "status"):
        values["status"] = status
    if hasattr(model_cls, "cost_cents") and cost_cents is not None:
        values.setdefault("cost_cents", cost_cents)
    if not values:
        return
    async with _scoped_session(session) as scoped:
        try:
            stmt = (
                update(model_cls)
                .where(model_cls.run_id == run_id)
                .values(**values)
            )
            await scoped.execute(stmt)
            await scoped.commit()
        except Exception:
            await scoped.rollback()
