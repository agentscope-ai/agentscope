# services/audit.py
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert, update

from ..db import models

ARTIFACTS_ROOT = Path("artifacts/audit")


def _now_iso() -> str:
    return time.strftime("%FT%TZ", time.gmtime())


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def audit_start_sync(session: AsyncSession, run_id: str, rel_id: str, stage: str, model_ctx: dict) -> None:
    # Fire-and-forget insert; ignore errors if table missing
    try:
        stmt = insert(models.__dict__.get("AuditRun2", models.AuditRun)).values(
            run_id if hasattr(models.AuditRun, "run_id") else run_id
        )
    except Exception:
        pass


@contextmanager
def audit_stage(rel_id: str, stage: str, model_ctx: dict):
    ts = int(time.time() * 1000)
    run_id = f"{rel_id}_{stage}_{ts}"
    root = ARTIFACTS_ROOT / run_id
    _ensure_dir(root)

    try:
        def dump(name: str, payload: Any) -> None:
            p = root / f"{name}.json"
            with p.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        yield dump, run_id
    finally:
        pass
