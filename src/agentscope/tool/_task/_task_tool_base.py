# -*- coding: utf-8 -*-
"""The task tool base class, providing unified interface and permission
check for builtin task related tools."""
from typing import Any

from .._base import ToolBase
from ...state import AgentState, Task
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)


class _TaskToolBase(ToolBase):
    name: str

    description: str

    input_schema: dict

    is_concurrency_safe: bool = True

    is_read_only: bool = False

    is_state_injected: bool = True

    is_external_tool: bool = False

    is_mcp: bool = False

    mcp_name: str | None = None

    @staticmethod
    def _get_active_blocker_ids(
        task: Task,
        agent_state: AgentState,
    ) -> list[str]:
        """Return blocker IDs whose tasks are not completed."""
        task_states = {
            existing_task.id: existing_task.state
            for existing_task in agent_state.tasks_context.tasks
        }
        return [
            blocker_id
            for blocker_id in task.blocked_by
            if task_states.get(blocker_id) != "completed"
        ]

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
