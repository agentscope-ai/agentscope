# -*- coding: utf-8 -*-
"""The task list tool class."""

from ._task_tool_base import _TaskToolBase
from .._response import ToolChunk
from .._base import _ParamsBase
from ...state import AgentState
from ...exception import DeveloperOrientedException
from ...message import TextBlock


class _TaskListParams(_ParamsBase):
    """The params of the list task params."""


class TaskList(_TaskToolBase):
    """List tasks for the agent to perform."""

    name: str = "TaskList"

    # pylint: disable=line-too-long
    description: str = """Use this tool to list all tasks in the task list.

## When to Use This Tool
- To see what tasks are available to work on (status: 'pending', no owner, not blocked)
- To check overall progress on the project
- To find tasks that are blocked and need dependencies resolved
- After completing a task, to check for newly unblocked work or claim the next available task
- **Prefer working on tasks in ID order** (lowest ID first) when multiple tasks are available, as earlier tasks often set up context for later ones

## Output

Returns a summary of each task:
- **id**: Task identifier (use with TaskGet, TaskUpdate)
- **subject**: Brief description of the task
- **status**: 'pending', 'in_progress', or 'completed'
- **owner**: Agent ID if assigned, empty if available
- **blockedBy**: List of open task IDs that must be resolved first (tasks with blockedBy cannot be claimed until dependencies resolve)

Use TaskGet with a specific task ID to view full details including description and comments."""  # noqa: E501

    input_schema: dict = _TaskListParams.model_json_schema()

    async def __call__(self, _agent_state: AgentState) -> ToolChunk:
        """List tasks for the agent to perform."""
        if not isinstance(_agent_state, AgentState):
            # Expose error to the developer
            raise DeveloperOrientedException(
                f"Error: TaskList requires AgentState to be provided, got "
                f"{_agent_state} instead.",
            )

        if len(_agent_state.tasks_context.tasks) == 0:
            return ToolChunk(
                content=[TextBlock(text="No tasks available.")],
            )

        tasks = []
        for task in _agent_state.tasks_context.tasks:
            owner = f"({task.owner})" if task.owner else ""
            blocked = (
                f'[blocked by {", ".join(task.blocked_by)}]'
                if task.blocked_by
                else ""
            )
            tasks.append(
                f"#{task.id} [{task.state}] {task.subject}{owner}{blocked}",
            )

        return ToolChunk(
            content=[
                TextBlock(text="\n".join(tasks)),
            ],
        )
