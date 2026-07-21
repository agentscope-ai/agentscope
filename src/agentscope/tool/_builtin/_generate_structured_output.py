# -*- coding: utf-8 -*-
""""""
from typing import Type, List

from aiohttp.web_middlewares import middleware
from pydantic import ValidationError, BaseModel

from .._utils import _remove_title_field
from ...message import TextBlock, ToolResultState
from .._base import ToolBase, ToolMiddlewareBase
from .._response import ToolChunk
from ...state import AgentState


class GenerateStructuredOutput(ToolBase):
    """The tool used to generate structured output."""

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

    def __init__(self, schema: Type[BaseModel], middlewares: List[ToolMiddlewareBase] | None = None,) -> None:
        """Initialize the tool by providing a schema."""

        super().__init__(middlewares=middlewares)

        self.schema = schema

    @property
    def input_schema(self) -> dict:
        """The input schema of this tool."""
        return _remove_title_field(self.schema.model_json_schema())

    async def _call_api(self, output: dict, _agent_state: AgentState) -> ToolChunk:
        """Generate a structured output from the given value."""

        if not _agent_state.reply_context.structured_output:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="No structured output is required for now."
                    )
                ],
                state=ToolResultState.SUCCESS,
            )

        try:
            res = _agent_state.reply_context.structured_output.output_schema.model_validate(
                output
            )
            _agent_state.reply_context.cur_structured_output = res

            # Clean the state of the structured output

            return ToolChunk(
                content=[
                    TextBlock(
                        text="Structured output generated successfully."
                    )
                ],
                state=ToolResultState.SUCCESS,
            )

        except ValidationError as e:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"ValidationError: Structured output validation failed with error: {e}"
                    )
                ],
                state=ToolResultState.ERROR,
            )
