# -*- coding: utf-8 -*-
"""The builtin tool used to generate the required structured output."""
from copy import deepcopy
from typing import Any, Generator, List, Type

from jsonschema import Draft202012Validator, validators
from pydantic import BaseModel, ValidationError

from ..permission._context import PermissionContext
from ..permission._decision import PermissionDecision
from ..permission._types import PermissionBehavior

from ..tool._utils import _remove_title_field
from ..message import TextBlock, ToolResultState
from ..tool._base import ToolBase, ToolMiddlewareBase
from ..tool._response import ToolChunk
from ..state import AgentState


def _extend_with_default(validator_class: Any) -> Any:
    """Extend a jsonschema validator to fill the schema-declared defaults
    into the instance during validation."""
    validate_properties = validator_class.VALIDATORS["properties"]

    def set_defaults(
        validator: Any,
        properties: dict,
        instance: Any,
        schema: dict,
    ) -> Generator[Any, None, None]:
        for prop, subschema in properties.items():
            if isinstance(instance, dict) and "default" in subschema:
                instance.setdefault(prop, deepcopy(subschema["default"]))
        yield from validate_properties(validator, properties, instance, schema)

    return validators.extend(validator_class, {"properties": set_defaults})


_DefaultFillingValidator = _extend_with_default(Draft202012Validator)


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
        schema: Type[BaseModel] | dict,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the tool with a model class or a JSON schema dict."""
        super().__init__(middlewares=middlewares)
        self.schema = schema

    @property
    def input_schema(self) -> dict:  # type: ignore[override]
        """The input schema of this tool."""
        if isinstance(self.schema, type):
            return _remove_title_field(self.schema.model_json_schema())
        return _remove_title_field(deepcopy(self.schema))

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """The generate structured output tool is always allowed
        to be called."""
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

        schema = _agent_state.reply_context.structured_schema
        if not schema:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="No structured output is required for now.",
                    ),
                ],
                state=ToolResultState.SUCCESS,
            )

        if isinstance(schema, type):
            # In-process: validate with the model class itself
            try:
                validated = schema.model_validate(kwargs)
            # Custom validators may raise arbitrary exceptions
            except Exception as e:  # pylint: disable=broad-exception-caught
                if isinstance(e, ValidationError):
                    message = "; ".join(
                        f"{'.'.join(str(_) for _ in err['loc'])}: "
                        f"{err['msg']}"
                        for err in e.errors()
                    )
                else:
                    message = str(e)
                return ToolChunk(
                    content=[
                        TextBlock(
                            text="ValidationError: Structured output "
                            f"validation failed with error: {message}",
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            _agent_state.reply_context.structured_output = (
                validated.model_dump(mode="json")
            )
        else:
            # Reloaded state: only the JSON schema dict remains, and the
            # validation fills the schema-declared defaults into ``kwargs``
            validator = _DefaultFillingValidator(schema)
            errors = list(validator.iter_errors(kwargs))
            if errors:
                messages = "; ".join(
                    f"{_.json_path}: {_.message}" for _ in errors
                )
                return ToolChunk(
                    content=[
                        TextBlock(
                            text="ValidationError: Structured output "
                            f"validation failed with error: {messages}",
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            _agent_state.reply_context.structured_output = kwargs

        return ToolChunk(
            content=[
                TextBlock(
                    text="Structured output generated successfully.",
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
