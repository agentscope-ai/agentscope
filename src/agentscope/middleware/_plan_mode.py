# -*- coding: utf-8 -*-
"""Plan mode middleware and manager.

Enforces a read-only PLAN phase where the agent can only investigate and
draft a plan, but cannot mutate files or run mutating commands.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator, Callable, TYPE_CHECKING

from ._base import MiddlewareBase
from .._logging import logger
from ..message import Msg, TextBlock, ToolResultBlock
from ..tool import ToolResponse

if TYPE_CHECKING:
    from ..agent import Agent


class PlanModeManager:
    """Coordinates plan-mode state and plan-file storage.

    State lives in ``AgentState.plan_mode_active`` and
    ``AgentState.plan_file`` so it survives restarts.
    """

    DEFAULT_PLAN_DIR = "plans"
    DEFAULT_PLAN_FILE = "plans/PLAN.md"

    def __init__(self, plan_dir: str | None = None) -> None:
        self.plan_dir = plan_dir or self.DEFAULT_PLAN_DIR

    def is_plan_active(self, agent: "Agent") -> bool:
        return agent.state.plan_mode_active

    def enter(self, agent: "Agent") -> str:
        """Enter plan mode. Idempotent."""
        agent.state.plan_mode_active = True
        if not agent.state.plan_file:
            agent.state.plan_file = self.DEFAULT_PLAN_FILE
        logger.info("[PlanMode] Entered plan mode for agent %s", agent.name)
        return agent.state.plan_file

    def exit(self, agent: "Agent") -> None:
        """Exit plan mode. Idempotent."""
        agent.state.plan_mode_active = False
        logger.info("[PlanMode] Exited plan mode for agent %s", agent.name)

    def write_plan(self, agent: "Agent", content: str) -> str:
        """Write the plan markdown file."""
        path = agent.state.plan_file or self.DEFAULT_PLAN_FILE
        # Write relative to current working directory
        full_path = Path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        agent.state.plan_file = path
        logger.info("[PlanMode] Wrote plan to %s", path)
        return path

    def read_plan(self, agent: "Agent") -> str:
        """Read the current plan file."""
        path = agent.state.plan_file or self.DEFAULT_PLAN_FILE
        full_path = Path(path)
        if full_path.exists():
            return full_path.read_text(encoding="utf-8")
        return ""


class PlanModeMiddleware(MiddlewareBase):
    """Enforces read-only plan mode by blocking mutating tool calls.

    Injects a system reminder when plan mode is active and replaces blocked
    tool calls with synthetic DENIED results.
    """

    ALWAYS_ALLOWED: set[str] = {
        "plan_enter",
        "plan_write",
        "plan_exit",
        "todo_write",
        "agent_spawn",
        "agent_send",
        "agent_list",
        "task_output",
        "task_list",
    }

    DENY_MESSAGE = (
        "Blocked: you are in PLAN mode (read-only). You may investigate "
        "and run read-only tools, record your plan with plan_write, and "
        "call plan_exit when ready to execute. Do not modify files or run "
        "mutating commands until the plan is approved."
    )

    PLAN_BANNER = (
        "\n<system-reminder>\n"
        "PLAN MODE is active (read-only). Plan file: {plan_file}\n"
        "Investigate the problem and draft a plan, but do NOT modify files, "
        "run mutating commands, or otherwise change state. Record your plan "
        "with the plan_write tool. When the plan is complete, call plan_exit "
        "to ask the user for approval; only after approval will you return "
        "to BUILD mode and be able to make changes.\n"
        "ACT, do not just narrate: when you decide to record or finish the "
        "plan, call plan_write (or plan_exit) in the SAME step.\n"
        "</system-reminder>"
    )

    BUILD_HINT = (
        "\n<system-reminder>You have switched from PLAN to BUILD mode; "
        "the read-only restriction is lifted. An approved plan exists at "
        "{plan_file} — read it for the details, then EXECUTE it step by "
        "step until the task is complete.</system-reminder>"
    )

    def __init__(
        self,
        manager: PlanModeManager,
        read_only_resolver: Callable[[str], bool] | None = None,
        additional_allowed: set[str] | None = None,
    ) -> None:
        self.manager = manager
        self.read_only_resolver = read_only_resolver or (lambda _name: False)
        self.additional_allowed = additional_allowed or set()

    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        if self.manager.is_plan_active(agent):
            return current_prompt + self.PLAN_BANNER.format(
                plan_file=agent.state.plan_file or "plans/PLAN.md",
            )
        if agent.state.plan_file:
            # Build mode but plan exists — add hint
            return current_prompt + self.BUILD_HINT.format(
                plan_file=agent.state.plan_file,
            )
        return current_prompt

    async def on_acting(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        if not self.manager.is_plan_active(agent):
            async for item in next_handler():
                yield item
            return

        tool_call = input_kwargs.get("tool_call")
        if tool_call is None:
            async for item in next_handler():
                yield item
            return

        name = getattr(tool_call, "name", "")
        if self._is_allowed(name):
            async for item in next_handler():
                yield item
            return

        # Block the tool call with a synthetic DENIED result
        logger.info(
            "[PlanMode] Blocked mutating tool '%s' for agent %s",
            name,
            agent.name,
        )
        yield ToolResponse(
            content=self.DENY_MESSAGE,
            state="denied",
        )

    def _is_allowed(self, tool_name: str) -> bool:
        if tool_name in self.ALWAYS_ALLOWED:
            return True
        if tool_name in self.additional_allowed:
            return True
        if self.read_only_resolver(tool_name):
            return True
        return False
