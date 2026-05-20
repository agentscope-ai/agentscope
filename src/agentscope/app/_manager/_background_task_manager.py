# -*- coding: utf-8 -*-
"""The background task manager."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import shortuuid
from pydantic import BaseModel, Field

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from ...tool import ToolBase, ToolChunk
from ..._logging import logger


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
    """Manages background asyncio task lifecycle within the agent services.

    Responsibilities:

    - **Task registry**: track running tasks so they can be cancelled via
      :class:`TaskStop`.
    - **Task scheduling**: convenience method for creating a task from a
      plain coroutine with an optional completion callback.
    - **Result storage**: completed task results are stored here so that
      middleware can pull them on the next reasoning step.
    """

    def __init__(self) -> None:
        """Initialise the background task manager."""
        self.tasks: OrderedDict[str, BackgroundTask] = OrderedDict()
        self._completed_results: dict[str, list[Any]] = {}

    # ------------------------------------------------------------------
    # Result storage API
    # ------------------------------------------------------------------

    def push_result(self, session_id: str, result: Any) -> None:
        """Store a completed task result for *session_id*.

        Args:
            session_id (`str`):
                The target session.
            result (`Any`):
                The result object (type determined by the producer).
        """
        self._completed_results.setdefault(session_id, []).append(result)

    def pop_results(self, session_id: str) -> list[Any]:
        """Return and clear all completed results for *session_id*.

        Args:
            session_id (`str`):
                The session to query.

        Returns:
            `list[Any]`:
                Accumulated results, or ``[]`` if none.
        """
        return self._completed_results.pop(session_id, [])

    def has_results(self, session_id: str) -> bool:
        """Check whether *session_id* has pending completed results.

        Args:
            session_id (`str`):
                The session to check.

        Returns:
            `bool`:
                ``True`` if there are unconsumed results.
        """
        return bool(self._completed_results.get(session_id))

    # ------------------------------------------------------------------
    # Task registration
    # ------------------------------------------------------------------

    async def register_task(
        self,
        asyncio_task: asyncio.Task,
        session_id: str,
        agent_id: str,
        user_id: str,
        on_complete: Callable[[], Coroutine] | None = None,
    ) -> str:
        """Register an already-running asyncio task and return its id.

        A watcher coroutine is spawned that awaits *asyncio_task*; when it
        finishes *on_complete* (if provided) is awaited and the task entry
        is removed from the registry.

        Args:
            asyncio_task (`asyncio.Task`):
                The already-running task to register.
            session_id (`str`):
                The originating session id.
            agent_id (`str`):
                The name of the agent that owns the task.
            user_id (`str`):
                The user id of the originating request.
            on_complete (`Callable[[], Coroutine] | None`, optional):
                An async callable invoked when the task finishes normally.
                Not called when the task is cancelled.

        Returns:
            `str`:
                The generated task ID.
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

        async def _watch() -> None:
            logger.info(
                "Background task started: task_id=%s, session_id=%s, "
                "agent_id=%s",
                task_id,
                session_id,
                agent_id,
            )
            try:
                await asyncio_task
            except asyncio.CancelledError:
                logger.info(
                    "Background task cancelled: task_id=%s, session_id=%s, "
                    "agent_id=%s",
                    task_id,
                    session_id,
                    agent_id,
                )
                return
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Background task failed with exception: task_id=%s, "
                    "session_id=%s, agent_id=%s",
                    task_id,
                    session_id,
                    agent_id,
                    exc_info=True,
                )
            finally:
                self.tasks.pop(task_id, None)

            logger.info(
                "Background task completed: task_id=%s, session_id=%s, "
                "agent_id=%s",
                task_id,
                session_id,
                agent_id,
            )
            if on_complete is not None:
                await on_complete()

        asyncio.create_task(_watch())
        return task_id

    async def list_tools(self) -> list[ToolBase]:
        """List the background tasks related tools.

        Returns:
            `list[ToolBase]`:
                A list containing the :class:`TaskStop` tool.
        """
        return [TaskStop(self.tasks)]

    def cancel(self) -> None:
        """Cancel all running background tasks on application shutdown.

        Each task's asyncio task is cancelled.  The ``on_complete``
        callback will **not** be invoked for cancelled tasks.
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
