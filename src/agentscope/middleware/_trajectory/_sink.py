# -*- coding: utf-8 -*-
"""Sinks and readers for persisting and loading recorded trajectories."""

import asyncio
import json
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..._logging import logger
from ._schema import TrajectoryRecord


@runtime_checkable
class TrajectorySink(Protocol):
    """Protocol for destinations that consume completed trajectories."""

    async def write(self, trajectory: TrajectoryRecord) -> None:
        """Persist or enqueue a completed :class:`TrajectoryRecord`."""


class InMemoryTrajectorySink:
    """Thread-safe in-memory buffer storing recorded trajectories."""

    def __init__(self) -> None:
        """Initialize an empty in-memory trajectory sink."""
        self.trajectories: list[TrajectoryRecord] = []

    async def write(self, trajectory: TrajectoryRecord) -> None:
        """Append a trajectory to the in-memory list."""
        self.trajectories.append(trajectory)

    def clear(self) -> None:
        """Remove all recorded trajectories."""
        self.trajectories.clear()


class JsonlTrajectorySink:
    """Append-only JSONL file sink safe for async and multi-task writers."""

    def __init__(self, path: str | Path) -> None:
        """Initialize the JSONL sink targeting ``path``."""
        self.path = Path(path)
        self._lock = threading.Lock()

    def _write_line(self, line: str) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")

    async def write(self, trajectory: TrajectoryRecord) -> None:
        """Serialize and append one trajectory line off the event loop."""
        line = trajectory.to_json()
        await asyncio.to_thread(self._write_line, line)


def read_trajectories_jsonl(
    path: str | Path,
    *,
    skip_corrupt_last_line: bool = True,
) -> list[TrajectoryRecord]:
    """Load :class:`TrajectoryRecord` entries from a JSONL file.

    Args:
        path (`str | Path`):
            Path to the JSONL trajectory file.
        skip_corrupt_last_line (`bool`, defaults to `True`):
            If ``True``, ignore a truncated final line left behind by an
            interrupted writer instead of failing the whole file load.
    """
    file_path = Path(path)
    if not file_path.exists():
        return []

    raw_lines = [
        line
        for line in file_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records: list[TrajectoryRecord] = []
    last_idx = len(raw_lines) - 1
    for idx, raw_line in enumerate(raw_lines):
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            if skip_corrupt_last_line and idx == last_idx:
                logger.warning(
                    "[TrajectoryMiddleware] Skipping corrupt trailing line "
                    "in %s",
                    file_path,
                )
                break
            raise
        if isinstance(payload, dict):
            records.append(TrajectoryRecord.from_dict(payload))
    return records
