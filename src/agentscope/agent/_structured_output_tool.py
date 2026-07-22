# -*- coding: utf-8 -*-
"""The builtin tool used to generate the required structured output."""
from typing import Any, Type, List

from pydantic import ValidationError, BaseModel

from ..permission._context import PermissionContext
from ..permission._decision import PermissionDecision
from ..permission._types import PermissionBehavior

from ..tool._utils import _remove_title_field
from ..message import TextBlock, ToolResultState
from ..tool._base import ToolBase, ToolMiddlewareBase
from ..tool._response import ToolChunk
from ..state import AgentState


class _GenerateStructuredOutput(ToolBase):
    """The builtin tool used to generate structured output."""

    name = "GenerateStructuredOutput"

    description = """Generate the required structured output by this tool.

This tool is equipped only when you're required to generate structured output.
The input schema represents the required structured output.
When you are ready to generate a structured output, call this tool with the
structured output as input.
When you're equipped this tool, you MUST end your response with calling this
tool. Once this tool is called, your current response is finished and the
structured output is sent to the user.

# When to Use This Tool
- When you collect enough resources and information.
"""
    is_state_injected = True
    is_concurrency_safe = True
    is_read_only = True

    def __init__(
        self,
        schema: Type[BaseModel],
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the tool by providing a schema."""
        super().__init__(middlewares=middlewares)
        self.schema = schema

    @property
    def input_schema(self) -> dict:
        """The input schema of this tool."""
        return _remove_title_field(self.schema.model_json_schema())

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """The generate structured output tool is always allowed to be called."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed.",
        )

    async def call(  # type: ignore[override]
        self,
        _agent_state: AgentState,
        **kwargs: Any,
    ) -> ToolChunk:
        """Validate the given structured output and record it in the agent
        state. The tool input fields (defined by the required schema) arrive
        as keyword arguments."""

        if not _agent_state.reply_context.structured_output:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="No structured output is required for now.",
                    ),
                ],
                state=ToolResultState.SUCCESS,
            )

        try:
            res = _agent_state.reply_context.structured_output.model_validate(
                kwargs,
            )
            _agent_state.reply_context.cur_structured_output = res

            return ToolChunk(
                content=[
                    TextBlock(
                        text="Structured output generated successfully.",
                    ),
                ],
                state=ToolResultState.SUCCESS,
            )

        except ValidationError as e:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="ValidationError: Structured output validation "
                        f"failed with error: {e}",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
