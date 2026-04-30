# -*- coding: utf-8 -*-
"""The task updated tool class."""
from typing import Literal

from pydantic import BaseModel, Field

from ._task_tool_base import _TaskToolBase
from .._response import ToolChunk
from ...state import AgentState
from ...exception import DeveloperOrientedException
from ...message import TextBlock, ToolResultState


class _TaskUpdateParams(BaseModel):
    """The params of the update task."""

    task_id: str = Field(description="The task id.")
    subject: str | None = Field(
        default=None,
        description="New subject for the task",
    )
    description: str | None = Field(
        default=None,
        description="New description for the task",
    )
    add_blocks: list[str] | None = Field(
        default=None,
        description="Task IDs that this task blocks",
    )
    status: Literal[
        "pending",
        "in_progress",
        "completed",
        "deleted",
    ] | None = Field(
        default=None,
        description="New status for the task",
    )
    add_blocked_by: list[str] | None = Field(
        default=None,
        description="Task IDs that block this task",
    )
    owner: str | None = Field(
        default=None,
        description="New owner for the task",
    )
    metadata: dict | None = Field(
        default=None,
        description="Metadata keys to merge into the task. "
        "Set a key to null to delete it.",
    )


class TaskUpdate(_TaskToolBase):
    """The tool to update the agent task."""

    name: str = "TaskUpdate"

    description: str = """Use this tool to update a task in the task list.

## When to Use This Tool

**Mark tasks as resolved:**
- When you have completed the work described in a task
- When a task is no longer needed or has been superseded
- IMPORTANT: Always mark your assigned tasks as resolved when you finish them
- After resolving, call TaskList to find your next task

- ONLY mark a task as completed when you have FULLY accomplished it
- If you encounter errors, blockers, or cannot finish, keep the task as in_progress
- When blocked, create a new task describing what needs to be resolved
- Never mark a task as completed if:
  - Tests are failing
  - Implementation is partial
  - You encountered unresolved errors
  - You couldn't find necessary files or dependencies

**Delete tasks:**
- When a task is no longer relevant or was created in error
- Setting status to `deleted` permanently removes the task

**Update task details:**
- When requirements change or become clearer
- When establishing dependencies between tasks

## Fields You Can Update

- **status**: The task status (see Status Workflow below)
- **subject**: Change the task title (imperative form, e.g., "Run tests")
- **description**: Change the task description
- **owner**: Change the task owner (agent name)
- **metadata**: Merge metadata keys into the task (set a key to null to delete it)
- **add_blocks**: Mark tasks that cannot start until this one completes
- **add_blocked_by**: Mark tasks that must complete before this one can start

## Status Workflow

Status progresses: `pending` → `in_progress` → `completed`

Use `deleted` to permanently remove a task.

## Staleness

Make sure to read a task's latest state using `TaskGet` before updating it.

## Examples

Mark task as in progress when starting work:
```json
{"task_id": "1", "status": "in_progress"}
```

Mark task as completed after finishing work:
```json
{"task_id": "1", "status": "completed"}
```

Delete a task:
```json
{"task_id": "1", "status": "deleted"}
```

Claim a task by setting owner:
```json
{"task_id": "1", "owner": "my-name"}
```

Set up task dependencies:
```json
{"task_id": "2", "add_blocked_by": ["1"]}
```"""  # noqa: E501

    input_schema: dict = _TaskUpdateParams.model_json_schema()

    async def __call__(
        self,
        _agent_state: AgentState,
        task_id: str,
        subject: str | None = None,
        description: str | None = None,
        add_blocks: list[str] | None = None,
        status: Literal["pending", "completed", "in_progress", "deleted"]
        | None = None,
        add_blocked_by: list[str] | None = None,
        owner: str | None = None,
        metadata: dict | None = None,
    ) -> ToolChunk:
        """Update the agent task."""
        if not isinstance(_agent_state, AgentState):
            # Expose error to the developer
            raise DeveloperOrientedException(
                f"Error: {self.name} requires AgentState to be provided, got "
                f"{_agent_state} instead.",
            )

        index = None
        for i, task in enumerate(_agent_state.tasks_context.tasks):
            if task.id == task_id:
                index = i

        if index is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"TaskNotFoundError: "
                        f"The task {task_id} does not exist.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        updated_fields = []

        if subject:
            updated_fields.append("subject")
            _agent_state.tasks_context.tasks[index].subject = subject

        if description is not None:
            updated_fields.append("description")
            _agent_state.tasks_context.tasks[index].description = description

        existed_ids = [_.id for _ in _agent_state.tasks_context.tasks]
        if add_blocks:
            current_blocks = _agent_state.tasks_context.tasks[index].blocks
            new_blocks = [
                _
                for _ in add_blocks
                if _ not in current_blocks and _ in existed_ids
            ]
            if new_blocks:
                updated_fields.append("add_blocks")
                for block_id in new_blocks:
                    self._update_block_relation(
                        task_id,
                        block_id,
                        _agent_state,
                    )

        if add_blocked_by is not None:
            current_blocked_by = _agent_state.tasks_context.tasks[
                index
            ].blocked_by
            new_blocked_by = [
                _
                for _ in add_blocked_by
                if _ not in current_blocked_by and _ in existed_ids
            ]
            if new_blocked_by:
                updated_fields.append("add_blocked_by")
                for blocked_by_id in new_blocked_by:
                    self._update_block_relation(
                        blocked_by_id,
                        task_id,
                        _agent_state,
                    )

        if status:
            if status == "deleted":
                # Permanently remove the task
                _agent_state.tasks_context.tasks.pop(index)
                # Remove task id from all the blocks and blocked_by
                for task in _agent_state.tasks_context.tasks:
                    if task_id in task.blocks:
                        task.blocks.remove(task_id)

                    if task_id in task.blocked_by:
                        task.blocked_by.remove(task_id)
                return ToolChunk(
                    content=[
                        TextBlock(text=f"Task {task_id} has been deleted."),
                    ],
                )
            # Update the status
            updated_fields.append("status")
            _agent_state.tasks_context.tasks[index].state = status

        if owner is not None:
            updated_fields.append("owner")
            _agent_state.tasks_context.tasks[index].owner = owner

        if metadata:
            updated_fields.append("metadata")
            for k, v in metadata.items():
                if v is None:
                    _agent_state.tasks_context.tasks[index].metadata.pop(
                        k,
                        None,
                    )
                else:
                    _agent_state.tasks_context.tasks[index].metadata[k] = v

        if updated_fields:
            res = f'Update task #{task_id} {", ".join(updated_fields)}.'
        else:
            res = (
                f"No updates were made to the task #{task_id}. "
                f"Make sure you provided at least one field to update and "
                f"the values are correct."
            )

        if _agent_state.tasks_context.tasks[index].state == "completed":
            res += (
                "\n\nTask completed. Call TaskList now to find your next "
                "available task or see if your work unblocked others."
            )

        return ToolChunk(content=[TextBlock(text=res)])

    @staticmethod
    def _update_block_relation(
        block_id: str,
        blocked_by_id: str,
        _agent_state: AgentState,
    ) -> None:
        """Update the block relationship between the tasks.

        Args:
            block_id (`str`):
                The id of the task that blocks the other tasks.
            blocked_by_id (`str`):
                The id of the task blocked by the task of `block_id`.
            _agent_state (`AgentState`):
                The agent state to update.
        """
        # Update the blocks
        for task in _agent_state.tasks_context.tasks:
            if task.id == block_id and blocked_by_id not in task.blocks:
                task.blocks.append(blocked_by_id)

            if task.id == blocked_by_id and block_id not in task.blocked_by:
                task.blocked_by.append(block_id)
