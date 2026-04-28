# -*- coding: utf-8 -*-
"""The meta tool class."""
from typing import Any, TYPE_CHECKING

from pydantic import Field, create_model
from jinja2 import Template

from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from .._base import ToolBase
from .._types import ToolGroup
from ...exception import DeveloperOrientedException
from ...message import TextBlock


if TYPE_CHECKING:
    from ...agent import AgentState
else:
    AgentState = Any


class ResetTools(ToolBase):
    """A meta tool allows agent to self-manage its equipped tools by
    activating or deactivating tool groups dynamically."""

    name: str = "reset_tools"
    description: str = (
        "This tool allows you to reset your equipped tools based on your "
        "current task requirements. These tools are organized into different "
        "groups, and you can activate/deactivate them by specifying the "
        "boolean values for each group in the input.\n\n"
        "**Important: The input booleans are the final state of their "
        "corresponding tool groups, not incremental changes.** Any group not "
        "explicitly set to True will be deactivated, regardless of its "
        "previous state.\n\n"
        "**Best practice**: Actively manage your tool groups——activate only "
        "what you need for the current task, and promptly deactivate groups "
        "as soon as they are no longer needed to conserve context space.\n\n"
        "This tool will return the usage instructions for the activated tool "
        "groups, which you **MUST pay attention to and follow**. You can "
        "also reuse this tool to re-check the instructions."
    )
    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False

    def __init__(
        self,
        groups: dict[str, ToolGroup],
        response_template: str,
    ) -> None:
        """Initialize the meta tool with the current tool groups."""
        self.groups = groups
        self.response_template = response_template

    @property
    def input_schema(self) -> dict[str, Any]:  # type: ignore[override]
        """Dynamically generate the input schema based on the current
        available tool groups."""
        fields = {}
        for group_name, group in self.groups.items():
            if group_name == "basic":
                continue
            fields[group_name] = (
                bool,
                Field(
                    default=False,
                    description=group.description,
                ),
            )

        model = create_model("_DynamicModel", **fields)
        schema = model.model_json_schema()
        return schema

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """The meta tool is always allowed to be called."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="The meta tool is always allowed to be called.",
        )

    async def __call__(
        self,
        _agent_state: AgentState,
        **kwargs: Any,
    ) -> ToolChunk:
        """Activate or deactivate tool groups based on the input arguments,
        and return their usage instructions."""
        if _agent_state is None:
            raise DeveloperOrientedException(
                "Error: ResetTools requires state to be provided.",
            )

        # Deactivate all tool groups first
        _agent_state.tool_context.activated_groups.clear()

        to_activate = []
        for key, value in kwargs.items():
            if not isinstance(value, bool):
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"Invalid arguments: the argument {key} "
                            f"should be a bool value, but got {type(value)}.",
                        ),
                    ],
                )

            if value:
                to_activate.append(key)

        _agent_state.tool_context.activated_groups.extend(to_activate)

        template = Template(self.response_template)
        activated_groups = [
            self.groups[_] for _ in to_activate if _ in self.groups
        ]
        return ToolChunk(
            content=[
                TextBlock(
                    text=template.render(groups=activated_groups),
                ),
            ],
        )
