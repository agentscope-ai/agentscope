# -*- coding: utf-8 -*-
"""Plan mode control tools."""
from __future__ import annotations

from typing import Any

from .._base import ToolBase
from ...permission import PermissionDecision, PermissionBehavior
from .._response import ToolChunk


class PlanEnter(ToolBase):
    """Enter read-only PLAN mode."""

    name: str = "plan_enter"
    description: str = (
        "Enter PLAN mode (read-only design phase). "
        "In this mode you can only investigate and draft a plan. "
        "You cannot modify files or run mutating commands. "
        "Record your plan with plan_write and call plan_exit when ready."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_state_injected: bool = True

    def __init__(self, manager: Any) -> None:
        from ...middleware._plan_mode import PlanModeManager
        self.manager: PlanModeManager = manager

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    async def __call__(
        self,
        _agent_state: Any = None,
    ) -> ToolChunk:
        path = self.manager.enter(_agent_state)
        return ToolChunk(
            content=f"Entered PLAN mode. Plan file: {path}",
        )


class PlanWrite(ToolBase):
    """Write the current plan to the plan file."""

    name: str = "plan_write"
    description: str = (
        "Write (or overwrite) the plan markdown file. "
        "Use this to record your design decisions and next steps while in "
        "PLAN mode. The plan will be available after you exit PLAN mode."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The full markdown content of the plan.",
            },
        },
        "required": ["content"],
    }
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_state_injected: bool = True

    def __init__(self, manager: Any) -> None:
        from ...middleware._plan_mode import PlanModeManager
        self.manager: PlanModeManager = manager

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    async def __call__(
        self,
        content: str,
        _agent_state: Any = None,
    ) -> ToolChunk:
        path = self.manager.write_plan(_agent_state, content)
        return ToolChunk(
            content=f"Plan written to {path}",
        )


class PlanExit(ToolBase):
    """Exit PLAN mode and return to BUILD mode."""

    name: str = "plan_exit"
    description: str = (
        "Exit PLAN mode and return to BUILD mode (normal execution). "
        "The read-only restriction is lifted. "
        "The approved plan remains available for reference."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_state_injected: bool = True

    def __init__(self, manager: Any) -> None:
        from ...middleware._plan_mode import PlanModeManager
        self.manager: PlanModeManager = manager

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    async def __call__(
        self,
        _agent_state: Any = None,
    ) -> ToolChunk:
        self.manager.exit(_agent_state)
        plan_text = self.manager.read_plan(_agent_state)
        hint = ""
        if plan_text:
            hint = (
                "\nApproved plan:\n```markdown\n"
                f"{plan_text[:500]}\n"
                "```"
            )
        return ToolChunk(
            content=f"Exited PLAN mode. Back to BUILD mode.{hint}",
        )
