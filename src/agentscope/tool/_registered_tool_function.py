# -*- coding: utf-8 -*-
"""The data model for registered tool functions in AgentScope."""
from copy import deepcopy
from dataclasses import field, dataclass
from typing import Callable, Literal, Type

from pydantic import BaseModel

from ._response import ToolResponse
from .._utils._common import _remove_title_field
from ..message import ToolUseBlock
from ..types import ToolFunction, JSONSerializableObject


@dataclass
class RegisteredToolFunction:
    """The registered tool function class."""

    name: str
    """The name of the tool function."""
    group: str | Literal["basic"]
    """The belonging group of the tool function"""
    source: Literal["function", "mcp_server", "function_group"]
    """"The type of the tool function, can be `function` or `mcp_server`."""
    original_func: ToolFunction
    """The original function"""
    json_schema: dict
    """The JSON schema of the tool function, which is used to validate the """
    preset_kwargs: dict[str, JSONSerializableObject] = field(
        default_factory=dict,
    )
    """The preset keyword arguments, which won't be presented in the JSON
    schema and exposed to the user."""
    extended_model: Type[BaseModel] | None = None
    """The base model used to extend the JSON schema of the original tool
    function, so that we can dynamically adjust the tool function."""
    mcp_name: str | None = None
    """The name of the MCP, if the tool function comes from an MCP server."""
    postprocess_func: Callable[
        [ToolUseBlock, ToolResponse],
        ToolResponse | None,
    ] | None = None
    """The post-processing function that will be called after the tool
    function is executed, taking the tool call block and tool
    response as arguments. If it returns `None`, the tool result will be
    returned as is. If it returns a `ToolResponse`, the returned block
    will be used as the final tool response."""

    @property
    def extended_json_schema(self) -> dict:
        """Get the JSON schema of the tool function, if an extended model is
        set, the merged JSON schema will be returned."""
        if self.extended_model is None:
            return self.json_schema

        # Merge the extended model with the original JSON schema
        extended_schema = self.extended_model.model_json_schema()

        merged_schema = deepcopy(self.json_schema)

        _remove_title_field(  # pylint: disable=protected-access
            extended_schema,
        )

        for key, value in extended_schema["properties"].items():
            if key in self.json_schema["function"]["parameters"]["properties"]:
                raise ValueError(
                    f"The field `{key}` already exists in the original "
                    f"function schema of `{self.name}`. Try to use a "
                    "different name.",
                )

            merged_schema["function"]["parameters"]["properties"][key] = value

            if key in extended_schema.get("required", []):
                if "required" not in merged_schema["function"]["parameters"]:
                    merged_schema["function"]["parameters"]["required"] = []
                merged_schema["function"]["parameters"]["required"].append(key)
        return merged_schema
