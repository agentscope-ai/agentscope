# -*- coding: utf-8 -*-
"""The background task manager."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Self

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
        id (`str`):
            Auto-generated unique task identifier.
    """

    asyncio_task: asyncio.Task
    """The running asyncio task."""

    session_id: str
    """The session id of the background task."""

    agent_id: str
    """The agent that created the background task."""

    user_id: str
    """The user id of the originating request."""

    id: str = field(default_factory=shortuuid.uuid)
    """The background task id."""


class _TaskStopParams(BaseModel):
    """The params of the stop task."""

    task_id: str = Field(
        description="The task id of the stop task.",
    )


class TaskStop(ToolBase):
    """A tool to stop a running background task."""

    name: str = "TaskStop"
    """The tool name."""

    description: str = "Stop a background task by its task id."
    """The tool description."""

    input_schema: dict = _TaskStopParams.model_json_schema()
    """The input schema."""

    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, background_tasks: dict[str, BackgroundTask]) -> None:
        """Initialize the TaskStop tool.

        Args:
            background_tasks (`dict[str, BackgroundTask]`):
                A reference to the background tasks managed by the
                :class:`BackgroundTaskManager`.
        """
        self.background_tasks = background_tasks

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
        if task_id not in self.background_tasks:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"TaskNotFoundError: The task {task_id} "
                        f"does not exist.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        # Cancel and pop the task
        task = self.background_tasks.pop(task_id)
        task.asyncio_task.cancel()
        logger.info(
            "Background task stopped via TaskStop tool: task_id=%s, "
            "session_id=%s, agent_id=%s",
            task_id,
            task.session_id,
            task.agent_id,
        )
        return ToolChunk(
            content=[TextBlock(text=f"Task {task_id} stopped successfully.")],
            state=ToolResultState.SUCCESS,
        )


class BackgroundTaskManager:
    """Tracks background asyncio task lifecycle within the agent service.

    Responsibilities:

    - **Task registry**: track running tasks so they can be cancelled
      via :class:`TaskStop` and on application shutdown.
    - **Task scheduling**: convenience method for creating a task from
      a plain coroutine with an optional completion callback.

    Completion results are **not** stored here — producers deliver them
    via the :class:`MessageBus` inbox + wakeup path (same as team
    messages), so any process's :class:`WakeupDispatcher` can pick up
    the result and drive the next chat run.
    """

    def __init__(self) -> None:
        """Initialise the background task manager."""
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
    ) -> str:
        """Register an already-running asyncio task and return its id.

        The task auto-removes from the registry when it finishes (via
        ``add_done_callback``). Post-completion work (e.g. delivering
        the result back through the message bus) is the caller's
        responsibility — typically by spawning a separate watcher
        coroutine that awaits the same task.

        Args:
            asyncio_task (`asyncio.Task`):
                The already-running task to register.
            session_id (`str`):
                The originating session id.
            agent_id (`str`):
                The agent record id that owns the task.
            user_id (`str`):
                The user id of the originating request.

        Returns:
            `str`:
                The generated task id.
        """
        bg_task = BackgroundTask(
            asyncio_task=asyncio_task,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
        )
        task_id = bg_task.id
        self.tasks[task_id] = bg_task
        logger.info(
            "Background task registered: task_id=%s, session_id=%s, "
            "agent_id=%s",
            task_id,
            session_id,
            agent_id,
        )
        asyncio_task.add_done_callback(
            lambda _t: self.tasks.pop(task_id, None),
        )
        return task_id

    async def list_tools(self) -> list[ToolBase]:
        """List the background tasks related tools.

        Returns:
            `list[ToolBase]`:
                A list containing the :class:`TaskStop` tool.
        """
        return [TaskStop(self.tasks)]

    # ------------------------------------------------------------------
    # Session-scoped cancel
    # ------------------------------------------------------------------

    def cancel_session_tasks(self, session_id: str) -> int:
        """Cancel every locally-tracked task whose owner session matches.

        Called by :class:`CancelDispatcher` on each incoming cancel
        broadcast. The dispatcher fires this on every process; each
        process only cancels the tasks it actually holds, so a session
        whose BG tasks all live on other processes is a silent no-op
        here. Returns the number of tasks cancelled on this process so
        callers can log the local effect.

        The matching tasks' ``add_done_callback`` from
        :meth:`register_task` removes them from ``self.tasks`` once the
        cancel actually takes effect, so no explicit pop is needed.
        Watcher coroutines (e.g.
        :class:`~agentscope.app._middleware.ToolOffloadMiddleware`'s
        ``_deliver_when_done``) catch :class:`asyncio.CancelledError`
        and skip result delivery, so cancelling here will not push a
        stale inbox/wakeup for a session being deleted.

        Args:
            session_id (`str`):
                The session whose tasks should be cancelled on this
                process.

        Returns:
            `int`:
                Number of tasks cancelled locally.
        """
        cancelled = 0
        for bg_task in list(self.tasks.values()):
            if bg_task.session_id != session_id:
                continue
            logger.info(
                "Cancelling background task for deleted/cancelled "
                "session: task_id=%s, session_id=%s, agent_id=%s",
                bg_task.id,
                bg_task.session_id,
                bg_task.agent_id,
            )
            bg_task.asyncio_task.cancel()
            cancelled += 1
        return cancelled

    async def __aenter__(self) -> Self:
        """Enter the async context. No setup required.

        Returns:
            `Self`: This manager instance.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Cancel all running background tasks on context exit.

        Each task's asyncio task is cancelled. Any watcher coroutine
        awaiting these tasks will see ``CancelledError`` and skip its
        post-completion work (e.g. result delivery).
        """
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
