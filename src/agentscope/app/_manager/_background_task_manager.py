# -*- coding: utf-8 -*-
"""The background task manager."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Coroutine, Any

import shortuuid
from pydantic import BaseModel, Field

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from agentscope.tool import ToolBase, ToolChunk


@dataclass
class _BackgroundTask:
    coroutine_task: Coroutine
    """The background task coroutine."""

    session_id: str
    """The session id of the background task."""

    agent_id: str
    """Who create the background task, incase one session has multiple agents.
    """

    id: str = field(default_factory=lambda: shortuuid.uuid())
    """The background task id."""


class _TaskStopParams(BaseModel):
    """The params of the stop task."""

    task_id: str = Field(
        description="The task id of the stop task.",
    )


class TaskStop(ToolBase):
    """A tool to stop the background task."""

    name: str = "TaskStop"

    description: str = """
"""

    input_schema: dict = _TaskStopParams.model_json_schema()

    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, background_tasks: dict[str, _BackgroundTask]) -> None:
        """Initialize the TaskStop tool.

        Args:
            background_tasks (`dict[str, _BackgroundTask]`):
                A reference to the background tasks managed by the
                `BackgroundTaskManager`.
        """
        self.background_tasks = background_tasks

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permission for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed to be called.",
        )

    async def __call__(self, task_id: str) -> ToolChunk:
        """Stop the background task.

        Args:
            task_id (str):
                The task id.

        Returns:
            `ToolChunk`:
                The tool chunk.
        """
        if task_id not in self.background_tasks:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"TaskNotFoundError: The task {task_id} does not exist.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        # Otherwise, stop and pop the task
        task = self.background_tasks.pop(task_id)
        task.coroutine_task.close()
        return ToolChunk(
            content=[TextBlock(text=f"Task {task_id} stopped successfully.")],
            state=ToolResultState.SUCCESS,
        )


class BackgroundTaskManager:
    """The background task manager, responsible for managing background tasks
    within the agent services."""

    def __init__(self) -> None:
        """Initialize the MCP manager."""
        self._tasks: OrderedDict[str, _BackgroundTask] = OrderedDict()

    async def add_task(
        self,
        agent_id: str,
        coroutine_task: Coroutine,
        callback: Coroutine,
    ) -> str:
        """Create an async background task and return its id, when finished
        the callback coroutine will be awaited.

        Args:
            agent_id (`str`):
                The agent ID.
            coroutine_task (`Coroutine`):
                The coroutine to create the background task.

        Returns:
            `str`:
                The created background task ID.
        """
        task = _BackgroundTask(
            coroutine_task=coroutine_task,
            session_id="",
            agent_id=agent_id,
        )
        self._tasks[task.id] = task

        async def _run_task():
            try:
                await task.coroutine_task
            finally:
                await callback()
                # Remove the task from the manager when it's done
                self._tasks.pop(task.id, None)

        # Run the task without awaiting it, so it runs in the background
        asyncio.create_task(_run_task())

        return task.id

    async def list_tools(self) -> list[ToolBase]:
        """List the background tasks related tools."""
        return [TaskStop(self._tasks)]
