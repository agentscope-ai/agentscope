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
        id (`str`):
            Auto-generated unique task identifier.
    """

    asyncio_task: asyncio.Task
    """The running asyncio task."""

    session_id: str
    """The session id of the background task."""

    agent_id: str
    """The agent that created the background task."""

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

    Result routing (e.g. writing results back into agent context) is
    intentionally **not** handled here — callers supply an ``on_complete``
    callback and own that logic themselves.
    """

    def __init__(self) -> None:
        """Initialise the background task manager."""
        self.tasks: OrderedDict[str, BackgroundTask] = OrderedDict()

    async def register_task(
        self,
        asyncio_task: asyncio.Task,
        session_id: str,
        agent_id: str,
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
        )
        task_id = bg_task.id
        self.tasks[task_id] = bg_task

        async def _watch() -> None:
            try:
                await asyncio_task
            except asyncio.CancelledError:
                return
            except Exception:  # pylint: disable=broad-except
                pass
            finally:
                self.tasks.pop(task_id, None)

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
        for bg_task in list(self.tasks.values()):
            bg_task.asyncio_task.cancel()
        self.tasks.clear()
