# -*- coding: utf-8 -*-
"""Workspace-backed task repository.

Uses :class:`WorkspaceManager` as the authoritative truth source for task
state, while maintaining in-memory asyncio task handles as a local
performance overlay for tasks running on the current node.

Storage layout: ``agents/<parentAgentId>/tasks/<sessionId>.json`` — a JSON
map of ``taskId → task record``.
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime, timezone
from typing import Any

from .._logging import logger


class WorkspaceTaskRepository:
    """Simplified workspace-backed task repository.

    Args:
        workspace_manager: The :class:`WorkspaceManager` instance.
        parent_agent_id: Identifier of the parent agent.
    """

    def __init__(
        self,
        workspace_manager: "WorkspaceManager",
        parent_agent_id: str,
    ) -> None:
        self._wm = workspace_manager
        self._parent_agent_id = parent_agent_id or "agent"
        # In-memory local task handles. Keyed by "<sessionId>:<taskId>".
        self._local_tasks: dict[str, asyncio.Task] = {}
        self._local_meta: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def put_task(
        self,
        runtime_context: dict[str, Any],
        task_id: str,
        session_id: str,
        coro: Coroutine[Any, Any, str],
    ) -> asyncio.Task:
        """Start a task, persist its PENDING record, and return the asyncio Task.

        .. warning::
            *coro* is consumed internally; the caller must not await it again.
        """
        record = self._make_record(task_id, session_id, "PENDING")
        await self._wm.write_task_record(
            runtime_context, self._parent_agent_id, session_id, record,
        )

        local_key = self._local_key(session_id, task_id)

        async def _wrapper() -> str:
            await self._update_status(
                runtime_context, session_id, task_id, "RUNNING", None, None,
            )
            try:
                result = await coro
                await self._update_status(
                    runtime_context, session_id, task_id, "COMPLETED", result, None,
                )
                return result
            except asyncio.CancelledError:
                await self._update_status(
                    runtime_context, session_id, task_id, "CANCELLED", None, None,
                )
                raise
            except Exception as e:
                err = str(e) or type(e).__name__
                await self._update_status(
                    runtime_context, session_id, task_id, "FAILED", None, err,
                )
                raise

        task = asyncio.create_task(_wrapper())
        self._local_tasks[local_key] = task
        self._local_meta[local_key] = {
            "session_id": session_id,
            "task_id": task_id,
            "started_at": _iso_now(),
        }
        return task

    async def get_task(
        self,
        runtime_context: dict[str, Any],
        session_id: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        """Return the latest task record from workspace."""
        return await self._wm.read_task_record(
            runtime_context, self._parent_agent_id, session_id, task_id,
        )

    async def list_tasks(
        self,
        runtime_context: dict[str, Any],
        session_id: str,
    ) -> list[dict[str, Any]]:
        """List all task records for a session."""
        return await self._wm.list_task_records(
            runtime_context, self._parent_agent_id, session_id,
        )

    async def cancel_task(
        self,
        runtime_context: dict[str, Any],
        session_id: str,
        task_id: str,
    ) -> bool:
        """Cancel a local task and set the CANCELLED flag in workspace."""
        local_key = self._local_key(session_id, task_id)
        task = self._local_tasks.get(local_key)
        if task is not None and not task.done():
            task.cancel()

        existing = await self._wm.read_task_record(
            runtime_context, self._parent_agent_id, session_id, task_id,
        )
        if existing is not None:
            existing["cancelRequested"] = True
            if existing.get("status") not in ("COMPLETED", "FAILED", "CANCELLED"):
                existing["status"] = "CANCELLED"
            await self._wm.write_task_record(
                runtime_context, self._parent_agent_id, session_id, existing,
            )
            return True
        return task is not None

    def remove_task(self, session_id: str, task_id: str) -> None:
        """Remove the local handle (does not touch workspace records)."""
        local_key = self._local_key(session_id, task_id)
        self._local_tasks.pop(local_key, None)
        self._local_meta.pop(local_key, None)

    def clear(self) -> None:
        """Clear all local handles."""
        self._local_tasks.clear()
        self._local_meta.clear()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _local_key(self, session_id: str, task_id: str) -> str:
        return f"{session_id}:{task_id}"

    def _make_record(
        self,
        task_id: str,
        session_id: str,
        status: str,
    ) -> dict[str, Any]:
        return {
            "taskId": task_id,
            "parentAgentId": self._parent_agent_id,
            "parentSessionId": session_id,
            "status": status,
            "createdAt": _iso_now(),
            "lastUpdatedAt": _iso_now(),
        }

    async def _update_status(
        self,
        runtime_context: dict[str, Any],
        session_id: str,
        task_id: str,
        status: str,
        result: str | None,
        error: str | None,
    ) -> None:
        existing = await self._wm.read_task_record(
            runtime_context, self._parent_agent_id, session_id, task_id,
        )
        # Terminal states are immutable
        if existing is not None and existing.get("status") in (
            "COMPLETED", "FAILED", "CANCELLED",
        ):
            return
        # If cancellation requested, don't overwrite with non-terminal
        if status not in ("COMPLETED", "FAILED", "CANCELLED"):
            if existing is not None and existing.get("cancelRequested"):
                return

        record = existing or self._make_record(task_id, session_id, status)
        record["status"] = status
        record["lastUpdatedAt"] = _iso_now()
        if result is not None:
            record["result"] = result
        if error is not None:
            record["errorMessage"] = error
        await self._wm.write_task_record(
            runtime_context, self._parent_agent_id, session_id, record,
        )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
