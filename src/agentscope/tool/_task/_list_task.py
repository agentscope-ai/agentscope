# -*- coding: utf-8 -*-
"""The task list tool class."""

from ._task_tool_base import _TaskToolBase
from .._response import ToolChunk
from .._base import ParamsBase
from ...state import AgentState
from ...exception import DeveloperOrientedException
from ...message import TextBlock


class _TaskListParams(ParamsBase):
    """The params of the list task params."""


class TaskList(_TaskToolBase):
    """List tasks for the agent to perform."""

    name: str = "TaskList"

    # pylint: disable=line-too-long
    description: str = """使用此工具列出任务列表中的所有任务。

## 何时使用此工具
- 查看有哪些任务可以处理（status：'pending'、无 owner、未被阻塞）
- 检查项目的整体进度
- 查找被阻塞且需要解决依赖关系的任务
- 完成一个任务后，检查是否有新解除阻塞的工作，或认领下一个可用的任务
- 当有多个任务可用时，**优先按 ID 顺序处理任务**（ID 最小的优先），因为较早的任务通常为较晚的任务建立上下文

## 输出

返回每个任务的摘要：
- **id**：任务标识符（与 TaskGet、TaskUpdate 配合使用）
- **subject**：任务的简要描述
- **status**：'pending'、'in_progress' 或 'completed'
- **owner**：已分配时的 Agent ID，可用时为空
- **blockedBy**：必须先解决的未完成任务 ID 列表（有 blockedBy 的任务在依赖关系解决之前不能被认领）

使用 TaskGet 并指定任务 ID 来查看包含描述和评论在内的完整详情。"""  # noqa: E501

    input_schema: dict = _TaskListParams.model_json_schema()

    async def call(self, _agent_state: AgentState) -> ToolChunk:
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
                f"{task.id} [{task.state}] {task.subject}{owner}{blocked}",
            )

        return ToolChunk(
            content=[
                TextBlock(text="\n".join(tasks)),
            ],
        )
