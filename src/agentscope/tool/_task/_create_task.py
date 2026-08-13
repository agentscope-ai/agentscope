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

    subject: str = Field(description="任务的简短标题")
    description: str = Field(description="需要做什么")
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="要附加到任务的任意元数据",
    )


class TaskCreate(_TaskToolBase):
    """Create a task for the agent to perform."""

    name: str = "TaskCreate"

    description: str = """使用此工具为当前会话创建结构化的任务列表。这有助于你跟踪进度、\
组织复杂任务，并向用户展示工作的周密性。
它还能帮助用户理解任务的进度以及其请求的整体进度。

## 何时使用此工具
请在以下场景中主动使用此工具：

- 复杂的多步骤任务——当任务需要 3 个或更多不同的步骤 \
或操作时
- 非琐碎且复杂的任务——需要仔细规划或 \
多个操作的任务
- 计划模式（Plan mode）——使用计划模式时，创建任务列表来跟踪工作
- 用户明确要求待办列表——当用户直接要求你 \
使用待办列表时
- 用户提供多个任务——当用户提供一系列要做的事情 \
（编号或逗号分隔）时
- 收到新指令后——立即将用户的需求 \
记录为任务
- 当你开始处理一个任务时——在开始工作之前 \
将其标记为 in_progress
- 完成一个任务后——将其标记为 completed，并添加在实现过程中发现的任何新的 \
后续任务

## 何时不要使用此工具

在以下情况跳过使用此工具：
- 只有一个**单一、直接**的任务
- 任务过于琐碎，跟踪它没有任何组织上的好处
- 任务可以在不到 3 个琐碎步骤内完成
- 任务纯粹是对话性或信息性的

请注意，如果只有一个琐碎的任务要做，你**不应该**使用此工具。\
在这种情况下，最好直接执行该任务。

## 任务字段

- **subject**：祈使句形式的简短、可操作的标题（例如 \
"Fix authentication bug in login flow"）
- **description**：需要做什么

所有任务创建时状态均为 `pending`。

## 提示

- 创建具有清晰、具体主题的任务，描述期望的结果
- 创建任务后，如果需要，使用 TaskUpdate 设置依赖关系 \
（blocks/blockedBy）
- 先检查 TaskList，避免创建重复的任务"""

    input_schema: dict = _TaskCreateParams.model_json_schema()

    async def call(
        self,
        _agent_state: AgentState,
        subject: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolChunk:
        """Create the subtask and add it into the agent state."""
        if not isinstance(_agent_state, AgentState):
            # Expose error to the developer
            raise DeveloperOrientedException(
                f"Error: TaskCreate requires AgentState to be provided, got "
                f"{_agent_state} instead.",
            )

        try:
            # Derive the next sequential id from existing tasks.
            # Existing ids that look numeric are considered; any
            # non-numeric ids (e.g. legacy UUIDs) are ignored.
            max_numeric = 0
            for t in _agent_state.tasks_context.tasks:
                try:
                    max_numeric = max(max_numeric, int(t.id))
                except (ValueError, TypeError):
                    pass
            next_id = str(max_numeric + 1)

            task = Task(
                id=next_id,
                subject=subject,
                description=description,
                metadata=metadata or {},
            )
            _agent_state.tasks_context.tasks.append(task)

            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Task (id={next_id}) created successfully: "
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
