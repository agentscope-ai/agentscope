# -*- coding: utf-8 -*-
"""The background task manager."""
import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Self, TYPE_CHECKING

import shortuuid
from pydantic import BaseModel, Field

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from agentscope.tool import ToolBase, ToolChunk
from agentscope._logging import logger

if TYPE_CHECKING:
    from ..message_bus import MessageBus


class TaskStatus(str, Enum):
    """Status of a background task throughout its lifecycle."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_MAX_RETAINED_TASKS = 50
# Maximum number of terminal-state tasks to keep for frontend polling.


@dataclass
class BackgroundTask:
    """Metadata for a single background task.

    Attributes:
        asyncio_task (`asyncio.Task`):
            The running asyncio task.
        session_id (`str`):
            The session id of the originating request.
        agent_id (`str`):
            The name of the agent that created the task.
        user_id (`str`):
            The user id of the originating request.
        tool_name (`str`):
            The name of the tool that was offloaded.
        id (`str`):
            Auto-generated unique task identifier.
        summary (`str`):
            A short human-readable summary of the tool invocation
            for frontend display (at most 128 characters).
        started_at (`float`):
            Unix epoch (UTC) when the task was registered.
        completed_at (`float | None`):
            Unix epoch (UTC) when the task reached a terminal state,
            or ``None`` while still running.
        status (`TaskStatus`):
            Current lifecycle status of the task.
        error_summary (`str | None`):
            Brief description of the failure reason when
            ``status == FAILED``.
    """

    asyncio_task: asyncio.Task
    """The running asyncio task."""

    session_id: str
    """The session id of the background task."""

    agent_id: str
    """The agent that created the background task."""

    user_id: str
    """The user id of the originating request."""

    tool_name: str
    """The name of the offloaded tool."""

    id: str = field(default_factory=shortuuid.uuid)
    """The background task id."""

    summary: str = ""
    """Short human-readable summary for frontend display."""

    started_at: float = field(default_factory=time.time)
    """Unix epoch (UTC) when the task was registered."""

    completed_at: float | None = None
    """Unix epoch (UTC) when the task finished, or None if running."""

    status: TaskStatus = TaskStatus.RUNNING
    """Current lifecycle status."""

    error_summary: str | None = None
    """Brief failure description (populated only for FAILED tasks)."""


class _ToolStopParams(BaseModel):
    """The params of the stop tool."""

    task_id: str = Field(
        description="The task id of the background tool to stop.",
    )


class ToolStop(ToolBase):
    """A tool to stop a running background tool execution."""

    name: str = "ToolStop"
    """The tool name."""

    description: str = (
        "Stop a background tool execution by its task id. "
        "Use this when you want to cancel a previously offloaded tool "
        "that is still running in the background."
    )
    """The tool description."""

    input_schema: dict = _ToolStopParams.model_json_schema()
    """The input schema."""

    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        background_tasks: dict[str, BackgroundTask],
        message_bus: "MessageBus",
        session_id: str,
    ) -> None:
        """Initialize the ToolStop tool.

        Args:
            background_tasks (`dict[str, BackgroundTask]`):
                A reference to the local background tasks managed by
                the :class:`BackgroundTaskManager`.
            message_bus (`MessageBus`):
                The application message bus, used to check the global
                registry and broadcast cross-worker cancel requests.
            session_id (`str`):
                The current session id, used to scope Redis registry
                lookups.
        """
        self.background_tasks = background_tasks
        self._message_bus = message_bus
        self._session_id = session_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permission for the tool usage.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input parameters.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                Always returns ALLOW.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed to be called.",
        )

    async def __call__(self, task_id: str) -> ToolChunk:
        """Stop the background task.

        Args:
            task_id (`str`):
                The task id.

        Returns:
            `ToolChunk`:
                The tool chunk.
        """
        # Path 1: task is on this worker — cancel directly.
        # Only cancel when the task belongs to the same session as this
        # ToolStop instance, so a leaked/guessed task_id from another
        # session cannot trigger cross-session cancellation on a shared
        # worker.
        local_task = self.background_tasks.get(task_id)
        if (
            local_task is not None
            and local_task.session_id == self._session_id
        ):
            self.background_tasks.pop(task_id, None)
            local_task.asyncio_task.cancel()
            logger.info(
                "Background task stopped via ToolStop (local): task_id=%s, "
                "session_id=%s, agent_id=%s",
                task_id,
                local_task.session_id,
                local_task.agent_id,
            )
            return ToolChunk(
                content=[
                    TextBlock(text=f"Task {task_id} stopped successfully."),
                ],
                state=ToolResultState.SUCCESS,
            )

        # Path 2: task exists in the global registry (another worker, or
        # a different session on this worker).
        if await self._message_bus.bg_task_exists(
            self._session_id,
            task_id,
        ):
            await self._message_bus.task_publish_cancel(task_id)
            logger.info(
                "Background task cancel broadcast via ToolStop (remote): "
                "task_id=%s, session_id=%s",
                task_id,
                self._session_id,
            )
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Cancel request sent for task {task_id}. "
                        f"The owning worker will stop it shortly.",
                    ),
                ],
                state=ToolResultState.SUCCESS,
            )

        # Path 3: task not found anywhere.
        return ToolChunk(
            content=[
                TextBlock(
                    text=f"TaskNotFoundError: The task {task_id} "
                    f"does not exist.",
                ),
            ],
            state=ToolResultState.ERROR,
        )


class BackgroundTaskManager:
    """Tracks background asyncio task lifecycle within the agent service.

    Responsibilities:

    - **Global registry**: register/unregister tasks in Redis so any
      process can query which tasks are alive for a session.
    - **Local handle cache**: hold ``asyncio.Task`` references for
      cancel and shutdown.
    - **Task scheduling**: convenience method for creating a task from
      a plain coroutine with a done callback that cleans up both sides.

    Completion results are delivered via the :class:`MessageBus` inbox
    + wakeup path (same as team messages), so any process's
    :class:`WakeupDispatcher` can pick up the result.
    """

    def __init__(self, message_bus: "MessageBus") -> None:
        """Initialise the background task manager.

        Args:
            message_bus (`MessageBus`):
                The application message bus; used for the global BG
                task registry (Redis Hash) and task-level cancel
                broadcasts.
        """
        self._message_bus = message_bus
        self.tasks: OrderedDict[str, BackgroundTask] = OrderedDict()

    # ------------------------------------------------------------------
    # Task registration
    # ------------------------------------------------------------------

    async def register_task(
        self,
        asyncio_task: asyncio.Task,
        session_id: str,
        agent_id: str,
        user_id: str,
        tool_name: str = "",
        *,
        summary: str = "",
    ) -> str:
        """Register an already-running asyncio task.

        Writes to both the local handle cache and the global Redis
        registry. On completion, updates the task status (rather than
        removing immediately) so frontends can observe terminal states
        via polling.

        Args:
            asyncio_task (`asyncio.Task`):
                The already-running task to register.
            session_id (`str`):
                The originating session id.
            agent_id (`str`):
                The agent record id that owns the task.
            user_id (`str`):
                The user id of the originating request.
            tool_name (`str`, optional):
                The name of the offloaded tool.
            summary (`str`, optional):
                Short human-readable summary of the tool invocation
                for frontend display.

        Returns:
            `str`:
                The generated task id.
        """
        bg_task = BackgroundTask(
            asyncio_task=asyncio_task,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            tool_name=tool_name,
            summary=summary,
        )
        task_id = bg_task.id
        self.tasks[task_id] = bg_task

        # Register in the global Redis registry.
        metadata = json.dumps(
            {
                "tool_name": tool_name,
                "agent_id": agent_id,
                "started_at": bg_task.started_at,
            },
        )
        await self._message_bus.bg_task_register(
            session_id,
            task_id,
            metadata,
        )

        logger.info(
            "Background task registered: task_id=%s, session_id=%s, "
            "agent_id=%s, tool_name=%s",
            task_id,
            session_id,
            agent_id,
            tool_name,
        )

        def _on_done(_t: asyncio.Task) -> None:
            bg = self.tasks.get(task_id)
            if bg is None:
                return

            bg.completed_at = time.time()
            if _t.cancelled():
                bg.status = TaskStatus.CANCELLED
            elif _t.exception() is not None:
                bg.status = TaskStatus.FAILED
                bg.error_summary = str(_t.exception())[:512]
            else:
                bg.status = TaskStatus.COMPLETED

            # Unregister from the global Redis registry.
            try:
                asyncio.ensure_future(
                    self._safe_bg_task_unregister(session_id, task_id),
                )
            except RuntimeError:
                # Event loop already closed during shutdown.
                pass

            self._evict_completed()

        asyncio_task.add_done_callback(_on_done)
        return task_id

    async def _safe_bg_task_unregister(
        self,
        session_id: str,
        task_id: str,
    ) -> None:
        """Unregister a finished background task, logging any failure.

        Args:
            session_id (`str`):
                The session id of the finished task.
            task_id (`str`):
                The task id to unregister from the global registry.
        """
        try:
            await self._message_bus.bg_task_unregister(
                session_id,
                task_id,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(
                "Failed to unregister background task from the global "
                "registry: task_id=%s, session_id=%s, error=%s",
                task_id,
                session_id,
                str(e),
            )

    def _evict_completed(self) -> None:
        """Remove the oldest terminal-state tasks when capacity is exceeded.

        Keeps at most :data:`_MAX_RETAINED_TASKS` completed/failed/cancelled
        tasks in the local registry so memory usage stays bounded while
        still giving frontends a window to observe terminal states.
        """
        completed = [
            tid
            for tid, t in self.tasks.items()
            if t.status != TaskStatus.RUNNING
        ]
        while len(completed) > _MAX_RETAINED_TASKS:
            oldest = completed.pop(0)
            self.tasks.pop(oldest, None)

    # ------------------------------------------------------------------
    # Task querying (for REST endpoints)
    # ------------------------------------------------------------------

    def list_tasks(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
    ) -> list[BackgroundTask]:
        """List tasks visible to a given user.

        Returns both running and recently-completed tasks that are
        still within the retention window.

        Args:
            user_id (`str`):
                Only return tasks belonging to this user.
            session_id (`str | None`, optional):
                If provided, further filter by session.

        Returns:
            `list[BackgroundTask]`:
                Matching tasks ordered by registration time.
        """
        return [
            t
            for t in self.tasks.values()
            if t.user_id == user_id
            and (session_id is None or t.session_id == session_id)
        ]

    def get_task(
        self,
        task_id: str,
        *,
        user_id: str,
    ) -> BackgroundTask | None:
        """Get a single task by id, scoped to the requesting user.

        Returns ``None`` when the task does not exist or belongs to a
        different user (no information leakage about task existence).

        Args:
            task_id (`str`):
                The task identifier.
            user_id (`str`):
                The requesting user's id.

        Returns:
            `BackgroundTask | None`:
                The task if found and owned by *user_id*, else ``None``.
        """
        t = self.tasks.get(task_id)
        if t is None or t.user_id != user_id:
            return None
        return t

    # ------------------------------------------------------------------
    # Tool listing
    # ------------------------------------------------------------------

    async def list_tools(self, session_id: str) -> list[ToolBase]:
        """List the background task tools for a given session.

        Args:
            session_id (`str`):
                The current session id (for ToolStop's registry
                lookups).

        Returns:
            `list[ToolBase]`:
                A list containing the :class:`ToolStop` tool.
        """
        return [ToolStop(self.tasks, self._message_bus, session_id)]

    # ------------------------------------------------------------------
    # Session-scoped cancel
    # ------------------------------------------------------------------

    def cancel_session_tasks(self, session_id: str) -> int:
        """Cancel every locally-tracked task whose owner session matches.

        Called by :class:`CancelDispatcher` on each incoming session
        cancel broadcast. Returns the number of tasks cancelled on this
        process.

        Args:
            session_id (`str`):
                The session whose tasks should be cancelled.

        Returns:
            `int`:
                Number of tasks cancelled locally.
        """
        cancelled = 0
        for bg_task in list(self.tasks.values()):
            if bg_task.session_id != session_id:
                continue
            logger.info(
                "Cancelling background task for session cancel: "
                "task_id=%s, session_id=%s, agent_id=%s",
                bg_task.id,
                bg_task.session_id,
                bg_task.agent_id,
            )
            bg_task.asyncio_task.cancel()
            cancelled += 1
        return cancelled

    # ------------------------------------------------------------------
    # Single-task cancel (called by CancelDispatcher on bus signal)
    # ------------------------------------------------------------------

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a single locally-tracked task by its id.

        Called by :class:`CancelDispatcher` when a task-level cancel
        broadcast arrives. Returns whether the task was found and
        cancelled on this process.

        Args:
            task_id (`str`):
                The task to cancel.

        Returns:
            `bool`:
                ``True`` if the task was found locally and cancelled.
        """
        bg_task = self.tasks.get(task_id)
        if bg_task is None:
            return False
        logger.info(
            "Cancelling background task via bus signal: "
            "task_id=%s, session_id=%s, agent_id=%s",
            task_id,
            bg_task.session_id,
            bg_task.agent_id,
        )
        bg_task.asyncio_task.cancel()
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Enter the async context. No setup required.

        Returns:
            `Self`: This manager instance.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Cancel all running background tasks on context exit."""
        count = len(self.tasks)
        logger.info(
            "Shutting down BackgroundTaskManager: cancelling %d task(s).",
            count,
        )
        for bg_task in list(self.tasks.values()):
            logger.info(
                "Cancelling background task on shutdown: task_id=%s, "
                "session_id=%s, agent_id=%s",
                bg_task.id,
                bg_task.session_id,
                bg_task.agent_id,
            )
            bg_task.asyncio_task.cancel()
        self.tasks.clear()
