# -*- coding: utf-8 -*-
"""The meta tool class."""
from typing import Any, List

from pydantic import Field, create_model
from jinja2 import Template

from .._tool_group import ToolGroup
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from .._base import ToolBase, ToolMiddlewareBase
from ...exception import DeveloperOrientedException
from ...message import TextBlock
from ...state import AgentState


class ResetTools(ToolBase):
    """A meta tool allows agent to self-manage its equipped tools by
    activating or deactivating tool groups dynamically."""

    name: str = "reset_tools"
    description: str = (
        "此工具允许你根据当前任务需求重新配置已装配的工具。这些工具被组织成"
        "不同的分组，你可以通过在输入中为每个分组指定布尔值来激活/停用它们。\n\n"
        "**重要提示：输入的布尔值是相应工具分组的最终状态，而不是增量更改。** "
        "任何未被显式设置为 True 的分组都将被停用，无论其之前的状态如何。\n\n"
        "**最佳实践**：主动管理你的工具分组——只激活当前任务所需的分组，并在"
        "不再需要时及时停用它们，以节省上下文空间。\n\n"
        "此工具将返回已激活工具分组的使用说明，你**必须注意并遵循这些说明**。"
        "你也可以重复使用此工具来重新查看这些说明。"
    )
    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = True

    def __init__(
        self,
        groups: list[ToolGroup],
        response_template: str,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the meta tool with the current tool groups."""
        super().__init__(middlewares=middlewares)
        self.groups = groups
        self.response_template = response_template

    @property
    def input_schema(self) -> dict[str, Any]:  # type: ignore[override]
        """Dynamically generate the input schema based on the current
        available tool groups."""
        fields = {}
        for group in self.groups:
            if group.name == "basic":
                continue
            fields[group.name] = (
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

    async def call(
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
        activated_groups = [_ for _ in self.groups if _.name in to_activate]
        return ToolChunk(
            content=[
                TextBlock(
                    text=template.render(groups=activated_groups),
                ),
            ],
        )
