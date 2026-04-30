# -*- coding: utf-8 -*-
"""The get task tool class."""
from pydantic import BaseModel, Field

from ._task_tool_base import _TaskToolBase
from .._response import ToolChunk
from ...state import AgentState
from ...exception import DeveloperOrientedException
from ...message import TextBlock, ToolResultState


class _TaskGetParams(BaseModel):
    """The params of the get task."""

    task_id: str = Field(description="The ID of the task to retrieve")


class TaskGet(_TaskToolBase):
    """Retrieve a task by its ID from the task list."""

    name: str = "TaskGet"

    description: str = """Use this tool to retrieve a task by its ID from the task list.

## When to Use This Tool

- When you need the full description and context before starting work on a task
- To understand task dependencies (what it blocks, what blocks it)
- After being assigned a task, to get complete requirements

## Output

Returns full task details:
- **subject**: Task title
- **description**: Detailed requirements and context
- **status**: 'pending', 'in_progress', or 'completed'
- **blocks**: Tasks waiting on this one to complete
- **blockedBy**: Tasks that must complete before this one can start

## Tips

- After fetching a task, verify its blockedBy list is empty before beginning work.
- Use TaskList to see all tasks in summary form."""  # noqa: E501

    input_schema: dict = _TaskGetParams.model_json_schema()

    async def __call__(
        self,
        task_id: str,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """Retrieve a task by its ID."""
        if not isinstance(_agent_state, AgentState):
            # Expose error to the developer
            raise DeveloperOrientedException(
                f"Error: TaskGet requires AgentState to be provided, got "
                f"{_agent_state} instead.",
            )

        # Find the task by ID
        task = None
        for t in _agent_state.tasks_context.tasks:
            if t.id == task_id:
                task = t
                break

        if task is None:
            return ToolChunk(
                content=[
                    TextBlock(text="Task not found"),
                ],
                state=ToolResultState.ERROR,
            )

        # Build the response
        lines = [
            f"Task #{task.id}: {task.subject}",
            f"Status: {task.state}",
            f"Description: {task.description}",
        ]

        if task.owner:
            lines.append(f"Owner: {task.owner}")

        if task.blocked_by:
            blocked_by_str = ", ".join([f"#{bid}" for bid in task.blocked_by])
            lines.append(f"Blocked by: {blocked_by_str}")

        if task.blocks:
            blocks_str = ", ".join([f"#{bid}" for bid in task.blocks])
            lines.append(f"Blocks: {blocks_str}")

        if task.metadata:
            lines.append(f"Metadata: {task.metadata}")

        return ToolChunk(
            content=[
                TextBlock(text="\n".join(lines)),
            ],
        )
