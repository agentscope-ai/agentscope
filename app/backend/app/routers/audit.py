from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import MetaData, Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session
from ..schemas.relations import (
    AuditArtifact,
    AuditResponse,
    AuditTimelineEntry,
    AuditTotals,
)


ARTIFACT_ROOT = Path("artifacts/audit")

router = APIRouter(prefix="/audit", tags=["audit"])


def _as_timezone(dt: Optional[datetime | str]) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, str):
        value = dt.strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _value_from_record(record: Mapping[str, Any] | object, key: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(key)
    return getattr(record, key, None)


def _infer_stage(record: Mapping[str, Any] | object) -> str:
    for attr in ("stage", "kind"):
        value = _value_from_record(record, attr)
        if isinstance(value, str) and value:
            return value

    run_id = _value_from_record(record, "run_id") or ""
    if "__" in run_id:
        parts = [p for p in run_id.split("__") if p]
        if len(parts) >= 2:
            return parts[-2]

    candidates = [p for p in run_id.split("_") if p]
    known = {
        "recall",
        "probe",
        "tri",
        "retrieve",
        "tri-retrieve",
        "tri_retrieve",
        "fuse",
        "judge",
        "score",
        "persist",
        "persist_relation",
    }
    for token in reversed(candidates):
        if token in known:
            return token

    return run_id or "unknown"


def _coerce_int(value: Optional[object]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_artifact_list(run_id: str) -> list[str]:
    run_root = ARTIFACT_ROOT / run_id
    if not run_root.exists() or not run_root.is_dir():
        return []

    files = sorted(p for p in run_root.glob("*.json") if p.is_file())
    return [str(p) for p in files]


def _accumulate_totals(
    totals: AuditTotals,
    *,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    total_tokens: Optional[int],
    cost_cents: Optional[int],
) -> None:
    if prompt_tokens is not None:
        totals.prompt_tokens += prompt_tokens
    if completion_tokens is not None:
        totals.completion_tokens += completion_tokens
    if total_tokens is not None:
        totals.total_tokens += total_tokens
    elif prompt_tokens is not None and completion_tokens is not None:
        totals.total_tokens += prompt_tokens + completion_tokens
    elif prompt_tokens is not None:
        totals.total_tokens += prompt_tokens
    elif completion_tokens is not None:
        totals.total_tokens += completion_tokens
    if cost_cents is not None:
        totals.cost_cents += cost_cents


async def _reflect_audit_table(session: AsyncSession) -> Optional[Table]:
    try:
        return await session.run_sync(
            lambda sync_session: Table(
                "audit_runs",
                MetaData(),
                autoload_with=sync_session.bind,
            )
        )
    except Exception:
        return None


def _first_int(record: Mapping[str, Any] | object, keys: Iterable[str]) -> Optional[int]:
    for key in keys:
        value = _value_from_record(record, key)
        coerced = _coerce_int(value)
        if coerced is not None:
            return coerced
    return None


@router.get("/{relation_id}", response_model=AuditResponse)
async def get_audit(
    relation_id: str,
    session: AsyncSession = Depends(get_session),
) -> AuditResponse:
    relation = await session.get(models.Relation, relation_id)
    if relation is None:
        raise HTTPException(status_code=404, detail="relation not found")

    audit_table = await _reflect_audit_table(session)
    relation_audit_table = models.RelationAudit.__table__

    if audit_table is None:
        rows: Iterable[Mapping[str, Any]] = []
    else:
        stmt = select(audit_table).join(
            relation_audit_table,
            relation_audit_table.c.run_id == audit_table.c.run_id,
        ).where(relation_audit_table.c.rel_id == relation_id)

        if "started_at" in audit_table.c:
            stmt = stmt.order_by(audit_table.c.started_at)
        elif "created_at" in audit_table.c:
            stmt = stmt.order_by(audit_table.c.created_at)
        else:
            stmt = stmt.order_by(audit_table.c.run_id)

        result = await session.execute(stmt)
        rows = result.mappings().all()

    timeline: list[AuditTimelineEntry] = []
    flat_artifacts: list[AuditArtifact] = []
    totals = AuditTotals()
    prompt_hash: Optional[str] = None
    input_hash: Optional[str] = None

    for row in rows:
        stage = _infer_stage(row)
        run_id = _value_from_record(row, "run_id")
        if not run_id:
            continue
        started_at = _as_timezone(_value_from_record(row, "started_at")) or _as_timezone(
            _value_from_record(row, "created_at")
        )
        completed_at = _as_timezone(_value_from_record(row, "completed_at"))
        prompt_tokens = _first_int(
            row,
            (
                "prompt_tokens",
                "input_tokens",
                "tokens_prompt",
                "tokens_input",
            ),
        )
        completion_tokens = _first_int(
            row,
            (
                "completion_tokens",
                "output_tokens",
                "tokens_completion",
                "tokens_output",
            ),
        )
        total_tokens = _first_int(
            row,
            (
                "total_tokens",
                "tokens_total",
            ),
        )
        if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens
        cost_cents = _first_int(row, ("cost_cents", "cost", "run_cost_cents"))

        artifacts = _build_artifact_list(run_id)
        for artifact_path in artifacts:
            flat_artifacts.append(AuditArtifact(step=stage, path=artifact_path))

        timeline.append(
            AuditTimelineEntry(
                stage=stage,
                run_id=run_id,
                started_at=started_at or _as_timezone(relation.created_at)
                or datetime.now(timezone.utc),
                completed_at=completed_at,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_cents=cost_cents,
                artifacts=artifacts,
            )
        )

        _accumulate_totals(
            totals,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_cents=cost_cents,
        )

        prompt_hash = prompt_hash or _value_from_record(row, "prompt_hash")
        input_hash = input_hash or _value_from_record(row, "input_hash")

    timeline.sort(key=lambda entry: entry.started_at)

    latest_run_id = timeline[-1].run_id if timeline else f"run_{relation_id}"
    created_at = _as_timezone(relation.created_at) or datetime.now(timezone.utc)

    notes: Optional[str]
    if timeline:
        notes = None
    else:
        notes = "未找到该关系的审计记录。"

    return AuditResponse(
        relation_id=relation_id,
        run_id=latest_run_id,
        status=relation.status,
        created_at=created_at,
        prompt_hash=prompt_hash,
        input_hash=input_hash,
        cost_cents=totals.cost_cents if timeline else None,
        artifacts=flat_artifacts,
        notes=notes,
        timeline=timeline,
        totals=totals,
    )
