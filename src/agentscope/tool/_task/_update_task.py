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

    task_id: str = Field(description="任务 ID。")
    subject: str | None = Field(
        default=None,
        description="任务的新主题",
    )
    description: str | None = Field(
        default=None,
        description="任务的新描述",
    )
    add_blocks: list[str] | None = Field(
        default=None,
        description="此任务所阻塞的任务 ID",
    )
    status: Literal[
        "pending",
        "in_progress",
        "completed",
        "deleted",
    ] | None = Field(
        default=None,
        description="任务的新状态",
    )
    add_blocked_by: list[str] | None = Field(
        default=None,
        description="阻塞此任务的任务 ID",
    )
    owner: str | None = Field(
        default=None,
        description="任务的新负责人",
    )
    metadata: dict | None = Field(
        default=None,
        description="要合并到任务中的元数据键。"
        "将某个键设置为 null 以将其删除。",
    )


class TaskUpdate(_TaskToolBase):
    """The tool to update the agent task."""

    name: str = "TaskUpdate"

    description: str = """使用此工具更新任务列表中的任务。

## 何时使用此工具

**将任务标记为已完成：**
- 当你完成了任务中描述的工作时
- 当任务不再需要或已被替代时
- 重要提示：完成分配给你的任务后，始终将其标记为已完成
- 解决后，调用 TaskList 寻找你的下一个任务

- 只有当你**完全**完成任务时，才将任务标记为 completed
- 如果遇到错误、阻塞或无法完成，请保持任务为 in_progress
- 当被阻塞时，创建一个新任务描述需要解决的内容
- 在以下情况下，绝不将任务标记为 completed：
  - 测试失败
  - 实现不完整
  - 遇到了未解决的错误
  - 找不到必要的文件或依赖

**删除任务：**
- 当任务不再相关或创建有误时
- 将状态设置为 `deleted` 会永久删除该任务

**更新任务详情：**
- 当需求发生变化或变得更加清晰时
- 当需要在任务之间建立依赖关系时

## 你可以更新的字段

- **status**：任务状态（参见下方的状态流转）
- **subject**：更改任务标题（祈使句形式，例如 "Run tests"）
- **description**：更改任务描述
- **owner**：更改任务负责人（agent 名称）
- **metadata**：将元数据键合并到任务中（将某个键设置为 null 以删除它）
- **add_blocks**：标记在该任务完成之前无法开始的任务
- **add_blocked_by**：标记必须在此任务开始之前完成的任务

## 状态流转

状态推进：`pending` → `in_progress` → `completed`

使用 `deleted` 永久删除任务。

## 过期状态

在更新任务之前，请务必使用 `TaskGet` 读取任务的最新状态。

## 示例

开始工作时将任务标记为进行中：
```json
{"task_id": "1", "status": "in_progress"}
```

完成工作后将任务标记为已完成：
```json
{"task_id": "1", "status": "completed"}
```

删除任务：
```json
{"task_id": "1", "status": "deleted"}
```

通过设置 owner 认领任务：
```json
{"task_id": "1", "owner": "my-name"}
```

设置任务依赖关系：
```json
{"task_id": "2", "add_blocked_by": ["1"]}
```"""  # noqa: E501

    input_schema: dict = _TaskUpdateParams.model_json_schema()

    async def call(
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
                        f"The task (id={task_id}) does not exist.",
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
                        TextBlock(
                            text=f"Task (id={task_id}) has been deleted.",
                        ),
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
            res = f'Update task (id={task_id}) {", ".join(updated_fields)}.'
        else:
            res = (
                f"No updates were made to the task (id={task_id}). "
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
