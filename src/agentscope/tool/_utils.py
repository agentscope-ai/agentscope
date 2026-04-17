# -*- coding: utf-8 -*-
"""The tool module utils."""
import inspect
from typing import Any, Dict, Callable

from docstring_parser import parse
from pydantic import Field, create_model, ConfigDict


def _remove_title_field(schema: dict) -> None:
    """Remove the title field from the JSON schema to avoid
    misleading the LLM."""
    # The top level title field
    if "title" in schema:
        schema.pop("title")

    # properties
    if "properties" in schema:
        for prop in schema["properties"].values():
            if isinstance(prop, dict):
                _remove_title_field(prop)

    # items
    if "items" in schema and isinstance(schema["items"], dict):
        _remove_title_field(schema["items"])

    # additionalProperties
    if "additionalProperties" in schema and isinstance(
        schema["additionalProperties"],
        dict,
    ):
        _remove_title_field(
            schema["additionalProperties"],
        )


def _extract_func_description(docstring: str) -> str:
    """Extract the function description from the docstring.

    Args:
        docstring (`str`):
            The docstring to extract the function description from.

    Returns:
        `str`:
            The extracted function description.
    """
    parsed_docstring = parse(docstring)
    descriptions = []
    if parsed_docstring.short_description is not None:
        descriptions.append(parsed_docstring.short_description)

    if parsed_docstring.long_description is not None:
        descriptions.append(parsed_docstring.long_description)

    return "\n".join(descriptions)


def _extract_input_schema(
    tool_func: Callable,
    include_var_positional: bool = False,
    include_var_keyword: bool = False,
) -> dict:
    """Extract input schema from the tool function's docstring

    Args:
        tool_func (`ToolFunction`):
            The tool function to extract the JSON schema from.
        include_var_positional (`bool`):
            Whether to include variable positional arguments in the JSON
            schema.
        include_var_keyword (`bool`):
            Whether to include variable keyword arguments in the JSON schema.

    Returns:
        `dict`:
            The extracted input JSON schema.
    """
    docstring = parse(tool_func.__doc__)
    params_docstring = {_.arg_name: _.description for _ in docstring.params}

    # Create a dynamic model with the function signature
    fields = {}
    for name, param in inspect.signature(tool_func).parameters.items():
        # Skip the `self` and `cls` parameters
        if name in ["self", "cls"]:
            continue

        # Handle `**kwargs`
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            if not include_var_keyword:
                continue

            fields[name] = (
                Dict[str, Any]
                if param.annotation == inspect.Parameter.empty
                else Dict[str, param.annotation],  # type: ignore
                Field(
                    description=params_docstring.get(
                        f"**{name}",
                        params_docstring.get(name, None),
                    ),
                    default={}
                    if param.default is param.empty
                    else param.default,
                ),
            )

        elif param.kind == inspect.Parameter.VAR_POSITIONAL:
            if not include_var_positional:
                continue

            fields[name] = (
                list[Any]
                if param.annotation == inspect.Parameter.empty
                else list[param.annotation],  # type: ignore
                Field(
                    description=params_docstring.get(
                        f"*{name}",
                        params_docstring.get(name, None),
                    ),
                    default=[]
                    if param.default is param.empty
                    else param.default,
                ),
            )

        else:
            fields[name] = (
                Any
                if param.annotation == inspect.Parameter.empty
                else param.annotation,
                Field(
                    description=params_docstring.get(name, None),
                    default=...
                    if param.default is param.empty
                    else param.default,
                ),
            )

    base_model = create_model(
        "_StructuredOutputDynamicClass",
        __config__=ConfigDict(arbitrary_types_allowed=True),
        **fields,
    )
    params_json_schema = base_model.model_json_schema()

    # Remove the title from the json schema
    _remove_title_field(params_json_schema)

    return params_json_schema
