# -*- coding: utf-8 -*-
"""The creating task tool class."""
from typing import Any

from pydantic import BaseModel, Field

from ._task_tool_base import _TaskToolBase
from .._response import ToolChunk
from ...state import AgentState, Task
from ...exception import DeveloperOrientedException
from ...message import TextBlock, ToolResultState


class _TaskCreateParams(BaseModel):
    """The params of the creating task tool."""

    subject: str = Field(description="A brief title for the task")
    description: str = Field(description="What needs to be done")
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Arbitrary metadata to attach to the task",
    )


class TaskCreate(_TaskToolBase):
    """Create a task for the agent to perform."""

    name: str = "TaskCreate"

    # pylint: disable=line-too-long
    description: str = """Use this tool to create a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

## When to Use This Tool

Use this tool proactively in these scenarios:

- Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
- Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
- Plan mode - When using plan mode, create a task list to track the work
- User explicitly requests todo list - When the user directly asks you to use the todo list
- User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
- After receiving new instructions - Immediately capture user requirements as tasks
- When you start working on a task - Mark it as in_progress BEFORE beginning work
- After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

## When NOT to Use This Tool

Skip using this tool when:
- There is only a single, straightforward task
- The task is trivial and tracking it provides no organizational benefit
- The task can be completed in less than 3 trivial steps
- The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

## Task Fields

- **subject**: A brief, actionable title in imperative form (e.g., "Fix authentication bug in login flow")
- **description**: What needs to be done

All tasks are created with status `pending`.

## Tips

- Create tasks with clear, specific subjects that describe the outcome
- After creating tasks, use TaskUpdate to set up dependencies (blocks/blockedBy) if needed
- Check TaskList first to avoid creating duplicate tasks"""  # noqa: E501

    input_schema: dict = _TaskCreateParams.model_json_schema()

    async def __call__(
        self,
        _agent_state: AgentState,
        subject: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolChunk:
        """Create the subtask and added it into the agent state."""
        if not isinstance(_agent_state, AgentState):
            # Expose error to the developer
            raise DeveloperOrientedException(
                f"Error: TaskCreate requires AgentState to be provided, got "
                f"{_agent_state} instead.",
            )

        try:
            task = Task(
                subject=subject,
                description=description,
                metadata=metadata or {},
            )
            _agent_state.tasks_context.tasks.append(task)

            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Task {task.id} created successfully: "
                        f"{task.subject}",
                    ),
                ],
            )
        except Exception as e:
            return ToolChunk(
                content=[
                    TextBlock(text=f"CreateTaskError: {e}"),
                ],
                state=ToolResultState.ERROR,
            )
