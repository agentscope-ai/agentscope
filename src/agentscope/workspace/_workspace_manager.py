# -*- coding: utf-8 -*-
"""Stateless accessor for workspace content using a two-layer read architecture.

Read path
    For every read (AGENTS.md, MEMORY.md, knowledge, etc.), the
    :class:`AbstractFilesystem` is queried first. If it returns non-empty
    content, that content is used (filesystem overrides). Otherwise, the local
    workspace disk is read as a fallback.

Write path
    All writes go through the :class:`AbstractFilesystem` when available;
    otherwise they fall back to local disk.

Expected layout::

    workspace/
    ├── AGENTS.md
    ├── MEMORY.md
    ├── memory/YYYY-MM-DD.md
    ├── skills/<skill-name>/SKILL.md
    ├── knowledge/KNOWLEDGE.md
    ├── knowledge/*
    ├── subagents/<id>.md                     (subagent declarations)
    ├── agents/<agentId>/workspace/           (isolated subagent runtime root)
    ├── agents/<agentId>/sessions/sessions.json
    └── agents/<agentId>/sessions/<sessionId>.log.jsonl
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from ..filesystem import AbstractFilesystem
from ..filesystem._workspace_index import WorkspaceIndex
from .._logging import logger

AGENTS_MD = "AGENTS.md"
MEMORY_MD = "MEMORY.md"
MEMORY_DIR = "memory"
SKILLS_DIR = "skills"
KNOWLEDGE_DIR = "knowledge"
KNOWLEDGE_MD = "KNOWLEDGE.md"
AGENTS_DIR = "agents"
SESSIONS_DIR = "sessions"
SESSIONS_STORE = "sessions.json"
TASKS_DIR = "tasks"
SESSION_CONTEXT_EXT = ".context.jsonl"
SESSION_LOG_EXT = ".log.jsonl"


class WorkspaceManager:
    """Two-layer read/write manager for workspace content."""

    def __init__(
        self,
        workspace: str | Path,
        filesystem: AbstractFilesystem | None = None,
        index: WorkspaceIndex | None = None,
        owns_index: bool = False,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._fs = filesystem
        self._index = index
        self._owns_index = owns_index
        self._path_locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        """Release owned resources."""
        if self._owns_index and self._index is not None:
            await self._index.clear()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def validate(self) -> None:
        """Log warnings for missing key files."""
        if not self._workspace.is_dir():
            logger.warning(
                "Workspace directory does not exist: %s. "
                "Please create it and add AGENTS.md.",
                self._workspace,
            )
            return
        agents_exists = self._workspace.joinpath(AGENTS_MD).is_file()
        if not agents_exists and self._fs is not None:
            try:
                agents_exists = await self._fs.exists({}, AGENTS_MD)
            except Exception:
                pass
        if not agents_exists:
            logger.warning(
                "AGENTS.md not found in workspace: %s. "
                "AGENTS.md defines persona and local conventions for the agent.",
                self._workspace,
            )

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def resolve_runtime_data_path(
        self,
        runtime_context: dict[str, Any],
        relative_path: str,
    ) -> Path:
        """Resolve a workspace-relative path for runtime user data."""
        return self._workspace / _normalize_relative_path(relative_path)

    def get_memory_dir(self, runtime_context: dict[str, Any]) -> Path:
        return self.resolve_runtime_data_path(runtime_context, MEMORY_DIR)

    def get_skills_dir(self) -> Path:
        return self._workspace / SKILLS_DIR

    def get_knowledge_dir(self) -> Path:
        return self._workspace / KNOWLEDGE_DIR

    def get_session_dir(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
    ) -> Path:
        return self.resolve_runtime_data_path(
            runtime_context,
            f"{AGENTS_DIR}/{agent_id}/{SESSIONS_DIR}",
        )

    def resolve_session_file(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
        session_id: str,
    ) -> Path:
        return self.get_session_dir(runtime_context, agent_id) / f"{session_id}.json"

    def resolve_session_context_file(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
        session_id: str,
    ) -> Path:
        return self.get_session_dir(
            runtime_context, agent_id,
        ) / f"{session_id}{SESSION_CONTEXT_EXT}"

    def resolve_session_log_file(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
        session_id: str,
    ) -> Path:
        return self.get_session_dir(
            runtime_context, agent_id,
        ) / f"{session_id}{SESSION_LOG_EXT}"

    # ------------------------------------------------------------------
    # High-level reads
    # ------------------------------------------------------------------

    async def read_agents_md(
        self,
        runtime_context: dict[str, Any],
    ) -> str:
        return await self._read_with_override(runtime_context, AGENTS_MD)

    async def read_knowledge_md(
        self,
        runtime_context: dict[str, Any],
    ) -> str:
        return await self._read_with_override(
            runtime_context, f"{KNOWLEDGE_DIR}/{KNOWLEDGE_MD}",
        )

    async def read_memory_md(
        self,
        runtime_context: dict[str, Any],
    ) -> str:
        return await self._read_with_override(runtime_context, MEMORY_MD)

    async def read_managed_workspace_file_utf8(
        self,
        runtime_context: dict[str, Any],
        relative_path: str,
    ) -> str:
        normalized = _normalize_relative_path(relative_path)
        if not normalized:
            return ""
        resolved = (self._workspace / normalized).resolve()
        if not str(resolved).startswith(str(self._workspace)):
            return ""
        return await self._read_with_override(runtime_context, normalized)

    # ------------------------------------------------------------------
    # Listings (union of filesystem + local disk)
    # ------------------------------------------------------------------

    async def list_knowledge_files(
        self,
        runtime_context: dict[str, Any],
    ) -> list[Path]:
        relative_paths: set[str] = set()

        if self._fs is not None:
            glob = await self._fs.glob(runtime_context, "*", KNOWLEDGE_DIR)
            for p in glob.paths:
                rel = _normalize_relative_path(p)
                if rel:
                    relative_paths.add(rel)

        dir_path = self.get_knowledge_dir()
        if dir_path.is_dir():
            for root, _dirs, files in os.walk(str(dir_path)):
                for fname in files:
                    p = Path(root) / fname
                    rel = p.resolve().relative_to(self._workspace).as_posix()
                    relative_paths.add(rel)

        return sorted(
            [self._workspace / r for r in relative_paths],
            key=lambda p: str(p),
        )

    async def list_memory_file_paths(
        self,
        runtime_context: dict[str, Any],
    ) -> list[str]:
        paths: set[str] = set()

        if self._fs is not None:
            try:
                await self._fs.read(runtime_context, MEMORY_MD, 0, 1)
                paths.add(MEMORY_MD)
            except Exception:
                pass
            glob = await self._fs.glob(runtime_context, "*.md", MEMORY_DIR)
            for p in glob.paths:
                rel = _normalize_relative_path(p)
                if rel:
                    paths.add(rel)

        mem_file = self.resolve_runtime_data_path(runtime_context, MEMORY_MD)
        if mem_file.is_file():
            paths.add(MEMORY_MD)

        mem_dir = self.get_memory_dir(runtime_context)
        if mem_dir.is_dir():
            for p in mem_dir.iterdir():
                if p.is_file() and p.suffix == ".md":
                    paths.add(f"{MEMORY_DIR}/{p.name}")

        return sorted(paths)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def append_utf8_workspace_relative(
        self,
        runtime_context: dict[str, Any],
        relative_path: str,
        content: str,
    ) -> None:
        normalized = _normalize_relative_path(relative_path)
        if not normalized or content is None:
            return
        async with self._lock_for(normalized):
            if self._fs is None:
                await self._append_local_file(normalized, content)
                return
            existing = ""
            try:
                rr = await self._fs.read(runtime_context, normalized, 0, 0)
                existing = rr.content
            except Exception:
                pass
            merged = existing + content
            await self._fs.write(runtime_context, normalized, merged)

    async def write_utf8_workspace_relative(
        self,
        runtime_context: dict[str, Any],
        relative_path: str,
        content: str,
    ) -> None:
        if relative_path is None or content is None:
            return
        normalized = _normalize_relative_path(relative_path)
        if not normalized:
            return
        if self._fs is None:
            await self._write_local_file(normalized, content)
            return
        await self._fs.write(runtime_context, normalized, content)

    # ------------------------------------------------------------------
    # Session index
    # ------------------------------------------------------------------

    async def update_session_index(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
        session_id: str,
        summary: str,
    ) -> None:
        if not agent_id or not session_id:
            return
        rel = f"{AGENTS_DIR}/{agent_id}/{SESSIONS_DIR}/{SESSIONS_STORE}"
        async with self._lock_for(rel):
            existing = await self._read_writable_workspace_relative_utf8(
                runtime_context, rel,
            )
            root = _parse_session_store_or_empty(existing)
            sessions = root.setdefault("sessions", {})
            sessions[session_id] = {
                "summary": summary or "",
                "updatedAt": _iso_now(),
            }
            root.setdefault("version", 1)
            serialized = json.dumps(root, indent=2, ensure_ascii=False)
            await self.write_utf8_workspace_relative(
                runtime_context, rel, serialized,
            )

    # ------------------------------------------------------------------
    # Task records
    # ------------------------------------------------------------------

    async def write_task_record(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
        session_id: str,
        record: dict[str, Any],
    ) -> None:
        if not agent_id or not session_id or not record:
            return
        rel = _task_record_path(agent_id, session_id)
        async with self._lock_for(rel):
            data = await self._read_task_map(runtime_context, rel)
            task_id = record.get("taskId")
            if task_id is None:
                return
            record["lastUpdatedAt"] = _iso_now()
            data[task_id] = record
            await self._persist_task_map(runtime_context, rel, data)

    async def read_task_record(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
        session_id: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        if not agent_id or not session_id or not task_id:
            return None
        rel = _task_record_path(agent_id, session_id)
        async with self._lock_for(rel):
            data = await self._read_task_map(runtime_context, rel)
            return data.get(task_id)

    async def list_task_records(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        if not agent_id or not session_id:
            return []
        rel = _task_record_path(agent_id, session_id)
        async with self._lock_for(rel):
            data = await self._read_task_map(runtime_context, rel)
            return list(data.values())

    # ------------------------------------------------------------------
    # Sweep marker
    # ------------------------------------------------------------------

    async def read_sweep_marker(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
    ) -> str | None:
        if not agent_id:
            return None
        rel = _sweep_marker_path(agent_id)
        content = await self._read_writable_workspace_relative_utf8(
            runtime_context, rel,
        )
        content = content.strip() if content else ""
        return content or None

    async def write_sweep_marker(
        self,
        runtime_context: dict[str, Any],
        agent_id: str,
    ) -> None:
        if not agent_id:
            return
        rel = _sweep_marker_path(agent_id)
        await self.write_utf8_workspace_relative(
            runtime_context, rel, _iso_now(),
        )

    # ------------------------------------------------------------------
    # Skill helpers
    # ------------------------------------------------------------------

    async def write_draft_skill_file(
        self,
        runtime_context: dict[str, Any],
        relative_path: str,
        content: str,
    ) -> None:
        if relative_path is None or content is None:
            return
        normalized = _normalize_relative_path(relative_path)
        if not normalized:
            return
        await self.write_utf8_workspace_relative(
            runtime_context, normalized, content,
        )

    async def move_skill(
        self,
        runtime_context: dict[str, Any],
        from_relative: str,
        to_relative: str,
    ) -> bool:
        if from_relative is None or to_relative is None or self._fs is None:
            return False
        src = _normalize_relative_path(from_relative)
        dst = _normalize_relative_path(to_relative)
        if not src or not dst:
            return False
        try:
            await self._fs.move(runtime_context, src, dst)
            return True
        except Exception as e:
            logger.warning("move_skill %s → %s failed: %s", src, dst, e)
            return False

    # ------------------------------------------------------------------
    # Private: two-layer read
    # ------------------------------------------------------------------

    async def _read_with_override(
        self,
        runtime_context: dict[str, Any],
        relative_path: str,
    ) -> str:
        fs_content = await self._read_text_through_filesystem(
            runtime_context, relative_path,
        )
        if fs_content:
            return fs_content
        return await self._read_file_quietly(self._workspace / relative_path)

    async def _read_text_through_filesystem(
        self,
        runtime_context: dict[str, Any],
        file_path: str,
    ) -> str:
        if self._fs is None:
            return ""
        try:
            rr = await self._fs.read(runtime_context, file_path, 0, 0)
            return rr.content or ""
        except Exception:
            return ""

    async def _read_file_quietly(self, path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)
            return ""

    async def _read_writable_workspace_relative_utf8(
        self,
        runtime_context: dict[str, Any],
        relative_path: str,
    ) -> str:
        normalized = _normalize_relative_path(relative_path)
        if not normalized:
            return ""
        return await self._read_with_override(runtime_context, normalized)

    # ------------------------------------------------------------------
    # Private: local disk fallback
    # ------------------------------------------------------------------

    async def _append_local_file(self, relative_path: str, content: str) -> None:
        local = (self._workspace / relative_path).resolve()
        if not str(local).startswith(str(self._workspace)):
            logger.warning("Refusing to write outside workspace: %s", relative_path)
            return
        local.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._append_local_file_sync, local, content)
        if self._index is not None:
            stat = await asyncio.to_thread(local.stat)
            await self._index.upsert(relative_path, stat.st_size, stat.st_mtime)

    def _append_local_file_sync(self, local: Path, content: str) -> None:
        with open(local, "a", encoding="utf-8") as f:
            f.write(content)

    async def _write_local_file(self, relative_path: str, content: str) -> None:
        local = (self._workspace / relative_path).resolve()
        if not str(local).startswith(str(self._workspace)):
            logger.warning("Refusing to write outside workspace: %s", relative_path)
            return
        local.parent.mkdir(parents=True, exist_ok=True)
        temp = local.with_suffix(local.suffix + ".tmp." + uuid.uuid4().hex)
        try:
            await asyncio.to_thread(self._write_local_file_sync, temp, local, content)
            if self._index is not None:
                stat = await asyncio.to_thread(local.stat)
                await self._index.upsert(
                    relative_path, stat.st_size, stat.st_mtime,
                )
        except Exception as e:
            logger.warning("Failed to write %s: %s", local, e)
            try:
                await asyncio.to_thread(temp.unlink, missing_ok=True)
            except Exception:
                pass

    def _write_local_file_sync(self, temp: Path, local: Path, content: str) -> None:
        temp.write_text(content, encoding="utf-8")
        temp.replace(local)

    # ------------------------------------------------------------------
    # Private: task map helpers
    # ------------------------------------------------------------------

    async def _read_task_map(
        self,
        runtime_context: dict[str, Any],
        rel: str,
    ) -> dict[str, dict[str, Any]]:
        text = await self._read_writable_workspace_relative_utf8(runtime_context, rel)
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as e:
            logger.error("Failed to parse task record store %s: %s", rel, e)
        return {}

    async def _persist_task_map(
        self,
        runtime_context: dict[str, Any],
        rel: str,
        data: dict[str, dict[str, Any]],
    ) -> None:
        try:
            serialized = json.dumps(data, indent=2, ensure_ascii=False)
            await self.write_utf8_workspace_relative(runtime_context, rel, serialized)
        except Exception as e:
            logger.warning("Failed to write task record store %s: %s", rel, e)

    # ------------------------------------------------------------------
    # Private: concurrency
    # ------------------------------------------------------------------

    def _lock_for(self, path: str) -> asyncio.Lock:
        lock = self._path_locks.get(path)
        if lock is None:
            lock = asyncio.Lock()
            self._path_locks[path] = lock
        return lock


# ------------------------------------------------------------------
# Module helpers
# ------------------------------------------------------------------

def _normalize_relative_path(relative_path: str) -> str:
    if not relative_path:
        return ""
    s = relative_path.replace("\\", "/").lstrip("/")
    while s.startswith("/"):
        s = s[1:]
    return s


def _task_record_path(agent_id: str, session_id: str) -> str:
    return f"{AGENTS_DIR}/{agent_id}/{TASKS_DIR}/{session_id}.json"


def _sweep_marker_path(agent_id: str) -> str:
    return f"{AGENTS_DIR}/{agent_id}/{TASKS_DIR}/_sweep.marker"


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _parse_session_store_or_empty(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        logger.warning("Corrupt or unreadable session store, reinitializing")
    return {}
