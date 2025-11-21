from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import audit as audit_db

ARTIFACTS_ROOT = Path("artifacts/audit")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _json_dump(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


@asynccontextmanager
async def audit_stage(
    session: AsyncSession,
    rel_id: str,
    stage: str,
    model_ctx: Optional[dict[str, Any]] = None,
):
    model_ctx = model_ctx or {}
    started_at = datetime.now(timezone.utc)
    run_id = f"{rel_id}_{stage}_{int(started_at.timestamp() * 1000)}"

    root = ARTIFACTS_ROOT / run_id
    _ensure_dir(root)

    meta_path = root / "meta.json"
    meta = {
        "run_id": run_id,
        "relation_id": rel_id,
        "stage": stage,
        "started_at": started_at.isoformat(),
        "model_ctx": model_ctx,
    }

    try:
        _json_dump(meta_path, meta)
    except Exception:
        pass

    await audit_db.audit_runs_insert_start(
        session=session,
        run_id=run_id,
        relation_id=rel_id,
        stage=stage,
        model_ctx=model_ctx,
        started_at=started_at,
    )

    status = "success"

    def dump(name: str, payload: Any) -> None:
        try:
            _json_dump(root / f"{name}.json", payload)
        except Exception:
            pass

    try:
        yield dump, run_id
    except Exception:
        status = "error"
        raise
    finally:
        finished_at = datetime.now(timezone.utc)
        meta.update({"finished_at": finished_at.isoformat(), "status": status})
        try:
            _json_dump(meta_path, meta)
        except Exception:
            pass

        await audit_db.audit_runs_insert_finish(
            session=session,
            run_id=run_id,
            finished_at=finished_at,
            status=status,
        )
