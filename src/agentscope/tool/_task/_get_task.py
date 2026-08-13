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

    task_id: str = Field(description="要检索的任务的 ID")


class TaskGet(_TaskToolBase):
    """Retrieve a task by its ID from the task list."""

    name: str = "TaskGet"

    description: str = """使用此工具按 ID 从任务列表中检索任务。

## 何时使用此工具

- 当你在开始处理一个任务之前需要完整的描述和上下文时
- 为了理解任务之间的依赖关系（它阻塞了什么，什么阻塞了它）
- 在接到任务分配后，获取完整的需求

## 输出

返回完整的任务详情：
- **subject**：任务标题
- **description**：详细的需求和上下文
- **status**：'pending'、'in_progress' 或 'completed'
- **blocks**：等待此任务完成的任务
- **blockedBy**：必须在此任务开始之前完成的任务

## 提示

- 获取任务后，在开始工作之前，请验证其 blockedBy 列表是否为空。
- 使用 TaskList 查看所有任务的摘要形式。"""  # noqa: E501

    input_schema: dict = _TaskGetParams.model_json_schema()

    async def call(
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
            f"Task (id={task.id}): {task.subject}",
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
