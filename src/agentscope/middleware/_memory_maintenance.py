# -*- coding: utf-8 -*-
"""Middleware that performs periodic memory maintenance after each reply.

Mirrors the Java ``MemoryMaintenanceMiddleware``:
1. Archives expired daily memory files to ``memory/archive/``.
2. Runs LLM-based consolidation via ``MemoryConsolidator``.
3. Prunes old session log files.

All steps are throttled by a configurable minimum gap (default 30 min).
"""
from __future__ import annotations

import datetime
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator, Callable

from ._base import MiddlewareBase
from .._logging import logger

if TYPE_CHECKING:
    from ..agent import Agent
    from ..memory import MemoryConsolidator


class MemoryMaintenanceMiddleware(MiddlewareBase):
    """Periodic memory maintenance: archive, consolidate, prune.

    Usage::

        agent = Agent(
            ...,
            middlewares=[
                MemoryMaintenanceMiddleware(
                    consolidator=consolidator,
                    memory_dir="memory",
                    session_dir=".agentscope/sessions",
                    min_gap_seconds=1800,
                ),
            ],
        )

    Args:
        consolidator (`MemoryConsolidator | None`):
            Optional consolidator to merge daily ledgers into MEMORY.md.
        memory_dir (`str`):
            Directory containing daily memory files (default ``"memory"``).
        session_dir (`str | None`):
            Directory containing session log files to prune.
            If ``None``, session pruning is skipped.
        daily_retention_days (`int`):
            Days to keep daily files before archiving (default 90).
        session_retention_days (`int`):
            Days to keep session files before pruning (default 180).
        min_gap_seconds (`float`):
            Minimum seconds between two maintenance runs (default 1800 = 30 min).
    """

    DEFAULT_MIN_GAP: float = 1800.0  # 30 minutes

    def __init__(
        self,
        consolidator: "MemoryConsolidator | None" = None,
        memory_dir: str = "memory",
        session_dir: str | None = None,
        daily_retention_days: int = 90,
        session_retention_days: int = 180,
        min_gap_seconds: float | None = None,
    ) -> None:
        self._consolidator = consolidator
        self._memory_dir = Path(memory_dir)
        self._session_dir = Path(session_dir) if session_dir else None
        self._daily_retention_days = daily_retention_days
        self._session_retention_days = session_retention_days
        self._min_gap = min_gap_seconds if min_gap_seconds is not None else self.DEFAULT_MIN_GAP
        self._last_run_at: float = 0.0

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Run reply, then perform maintenance if throttle permits."""
        async for item in next_handler():
            yield item
        await self._maybe_run_maintenance()

    async def _maybe_run_maintenance(self) -> None:
        now = time.time()
        if now - self._last_run_at < self._min_gap:
            return
        self._last_run_at = now
        try:
            await self._run_maintenance()
        except Exception as e:
            logger.warning("[MemoryMaintenanceMiddleware] Maintenance failed: %s", e)

    async def _run_maintenance(self) -> None:
        logger.debug("[MemoryMaintenanceMiddleware] Running maintenance...")
        await self._archive_expired_daily_files()
        await self._consolidate_memory()
        await self._prune_old_sessions()
        logger.debug("[MemoryMaintenanceMiddleware] Maintenance completed")

    async def _archive_expired_daily_files(self) -> None:
        """Move daily files older than retention to memory/archive/."""
        if not self._memory_dir.exists():
            return
        cutoff = datetime.date.today() - datetime.timedelta(
            days=self._daily_retention_days
        )
        archive_dir = self._memory_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        for path in self._memory_dir.glob("*.md"):
            if path.name.startswith("."):
                continue
            # Skip MEMORY.md and non-date files
            base = path.stem
            try:
                file_date = datetime.date.fromisoformat(base)
            except ValueError:
                continue
            if file_date < cutoff:
                dest = archive_dir / path.name
                try:
                    shutil.move(str(path), str(dest))
                    logger.debug(
                        "[MemoryMaintenance] Archived expired daily file: %s",
                        path.name,
                    )
                except Exception as e:
                    logger.warning(
                        "[MemoryMaintenance] Failed to archive %s: %s",
                        path.name,
                        e,
                    )

    async def _consolidate_memory(self) -> None:
        """Run LLM-based consolidation if a consolidator is configured."""
        if self._consolidator is None:
            return
        try:
            ok = await self._consolidator.consolidate()
            if ok:
                logger.debug("[MemoryMaintenance] Consolidation completed")
        except Exception as e:
            logger.warning("[MemoryMaintenance] Consolidation failed: %s", e)

    async def _prune_old_sessions(self) -> None:
        """Delete session log files older than retention."""
        if self._session_dir is None or not self._session_dir.exists():
            return
        cutoff = time.time() - self._session_retention_days * 86400
        for path in self._session_dir.glob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
                if mtime < cutoff:
                    path.unlink()
                    logger.debug(
                        "[MemoryMaintenance] Pruned old session file: %s",
                        path.name,
                    )
            except Exception as e:
                logger.warning(
                    "[MemoryMaintenance] Failed to prune %s: %s",
                    path.name,
                    e,
                )
