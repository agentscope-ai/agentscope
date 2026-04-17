# -*- coding: utf-8 -*-
"""The types for the tool module in AgentScope."""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import (
    Literal,
    Type,
    Any,
    TypedDict,
    TypeAlias,
    Coroutine,
    AsyncGenerator,
    Generator,
    Awaitable,
    Callable,
)

from pydantic import BaseModel

from ._response import ToolChunk
from ._base import ToolBase
from .._utils._common import _remove_title_field


@dataclass
class RegisteredTool:
    """The registered tool function class, used to store the tool function and
    its registration information."""

    tool: ToolBase
    """The original tool function."""

    # Execution related fields
    extended_model: Type[BaseModel] | None = field(init=False, default=None)
    """The base model used to extend the JSON schema of the original tool
    function, so that we can dynamically adjust the tool function."""

    # Tools management fields
    group: str | Literal["basic"] = "basic"
    """The belonging group of the tool function"""
    original_name: str | None = field(default=None)
    """The original name of the tool function when it has been renamed."""

    def __post_init__(self) -> None:
        """Validate the registered tool function after initialization."""
        # validate schema
        if self.tool.input_schema is not None:
            assert (
                isinstance(self.tool.input_schema, dict)
                and self.tool.input_schema.get("type") == "object"
                and isinstance(self.tool.input_schema.get("properties"), dict)
            ), f"Invalid input_schema: {self.tool.input_schema}. "

    def get_function_schema(
        self,
        extended_model: Type[BaseModel] | None = None,
    ) -> dict:
        """Get the JSON schema of the tool function via the following steps:

        1. Remove preset_kwargs from the JSON schema, since they are not
        exposed to the agent.
        2. If extended_model is provided, merge its schema with the
        current function schema.

        Args:
            extended_model (`Type[BaseModel] | None`, optional):
                The dynamic BaseModel used to extend the original function. If
                provided, the given BaseModel will be merged into the original
                function schema instead of the extended_model field.

        Returns:
            `dict`: The JSON schema of the tool function.
        """
        input_schema = deepcopy(self.tool.input_schema)
        _remove_title_field(input_schema)
        function_schema: dict = {
            "type": "function",
            "function": {
                "name": self.tool.name,
                "description": self.tool.description,
                "parameters": input_schema,
            },
        }

        extended_model = extended_model or self.extended_model

        if extended_model is None:
            return function_schema

        # Merge the extended model with the original JSON schema
        extended_schema = extended_model.model_json_schema()

        _remove_title_field(extended_schema)

        # Merge properties from extended schema
        for key, value in extended_schema["properties"].items():
            if key in function_schema["function"]["parameters"]["properties"]:
                raise ValueError(
                    f"The field `{key}` already exists in the original "
                    f"function schema of `{self.tool.name}`. Try to use a "
                    "different name.",
                )

            function_schema["function"]["parameters"]["properties"][
                key
            ] = value

            if key in extended_schema.get("required", []):
                if "required" not in function_schema["function"]["parameters"]:
                    function_schema["function"]["parameters"]["required"] = []
                function_schema["function"]["parameters"]["required"].append(
                    key,
                )

        # Merge $defs from extended schema to support nested models
        if "$defs" in extended_schema:
            merged_params = function_schema["function"]["parameters"]
            if "$defs" not in merged_params:
                merged_params["$defs"] = {}

            # Check for conflicts and merge $defs
            for def_key, def_value in extended_schema["$defs"].items():
                def_value_copy = deepcopy(def_value)
                _remove_title_field(
                    def_value_copy,
                )  # pylint: disable=protected-access

                if def_key in merged_params["$defs"]:
                    # Check if the two definitions are from the same BaseModel
                    # by comparing their content
                    # Create copies and remove title fields for comparison

                    existing_def_copy = deepcopy(
                        merged_params["$defs"][def_key],
                    )
                    _remove_title_field(existing_def_copy)

                    if existing_def_copy != def_value_copy:
                        # The definitions are different, raise an error
                        raise ValueError(
                            f"The $defs key `{def_key}` conflicts with "
                            f"existing definition in function schema of "
                            f"`{self.tool.name}`.",
                        )
                    # The definitions are the same (from the same BaseModel),
                    # skip merging this key
                    continue

                merged_params["$defs"][def_key] = def_value_copy

        return function_schema


@dataclass
class ToolGroup:
    """The tool group abstraction that provides a higher level of organization
    for tools."""

    name: str
    """The group name, which will be used in the reset function as the group
    identifier."""
    description: str
    """The description of the tool group to tell the agent what the tool
    group is about."""
    instructions: str | None = None
    """The instructions that will be contained when this tool group is
    activated."""


class AgentSkill(TypedDict):
    """The agent skill typed dict class"""

    name: str
    """The name of the skill."""
    description: str
    """The description of the skill."""
    dir: str
    """The directory of the agent skill."""


# The function types that can be registered as tools in AgentScope.
Function: TypeAlias = (
    # Sync function
    Callable[..., ToolChunk]
    |
    # Async function
    Callable[..., Awaitable[ToolChunk]]
    |
    # Sync generator function
    Callable[..., Generator[ToolChunk, None, None]]
    |
    # Async generator function
    Callable[..., AsyncGenerator[ToolChunk, None]]
    |
    # Async function that returns async generator
    Callable[..., Coroutine[Any, Any, AsyncGenerator[ToolChunk, None]]]
    |
    # Async function that returns sync generator
    Callable[..., Coroutine[Any, Any, Generator[ToolChunk, None, None]]]
)


ToolChoice: TypeAlias = Literal["auto", "none", "required"] | str
