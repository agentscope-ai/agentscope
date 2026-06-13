# -*- coding: utf-8 -*-
"""The tool module utils."""
import inspect
from typing import Any, Dict, Callable

import jsonschema
from docstring_parser import parse
from pydantic import Field, create_model, ConfigDict


def _remove_title_field(schema: dict) -> dict:
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
        _remove_title_field(schema["additionalProperties"])

    # $defs — referenced sub-schemas, e.g. Pydantic models used as parameter
    # types generate "$defs": {"SubModel": {"title": "SubModel", ...}}.
    # These titles are auto-generated noise just like property titles, and
    # should be removed for the same reason.
    if "$defs" in schema and isinstance(schema["$defs"], dict):
        for def_schema in schema["$defs"].values():
            if isinstance(def_schema, dict):
                _remove_title_field(def_schema)

    return schema


def _extract_func_description(docstring: str) -> str:
    """Extract the function description from the docstring.

    Args:
        docstring (`str`):
            The docstring to extract the function description from.

    Returns:
        `str`:
            The extracted function description.
    """
    parsed_docstring = parse(docstring or "")
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
    docstring = parse(tool_func.__doc__ or "")
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


def _repair_arguments_by_schema(
    value: Any,
    schema: dict[str, Any] | None,
) -> Any:
    """Best-effort repair for tool arguments based on JSON schema.

    The function intentionally keeps a narrow scope: it only coerces common
    scalar mismatches and then recurses into objects and arrays. If a value
    cannot be repaired safely, the original value is returned and the normal
    JSON-schema validation/tool error path handles it.
    """
    return _repair_value_by_schema(value, schema or {}, schema or {})


def _repair_value_by_schema(
    value: Any,
    schema: dict[str, Any] | bool,
    root_schema: dict[str, Any],
) -> Any:
    if not isinstance(schema, dict):
        return value

    resolved_schema = _resolve_schema_ref(schema, root_schema)
    if resolved_schema is not schema:
        return _repair_value_by_schema(value, resolved_schema, root_schema)

    for union_key in ("anyOf", "oneOf"):
        if isinstance(schema.get(union_key), list):
            repaired = _repair_union_value(
                value,
                schema[union_key],
                root_schema,
            )
            if repaired is not _UNREPAIRED:
                return repaired

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        repaired = _repair_union_value(
            value,
            [{"type": type_name} for type_name in schema_type],
            root_schema,
        )
        if repaired is not _UNREPAIRED:
            return repaired
        return value

    if schema_type == "object" and isinstance(value, dict):
        repaired_obj = dict(value)
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, prop_schema in properties.items():
                if key in repaired_obj:
                    repaired_obj[key] = _repair_value_by_schema(
                        repaired_obj[key],
                        prop_schema,
                        root_schema,
                    )

        additional_schema = schema.get("additionalProperties")
        if isinstance(additional_schema, dict):
            for key in repaired_obj.keys() - properties.keys():
                repaired_obj[key] = _repair_value_by_schema(
                    repaired_obj[key],
                    additional_schema,
                    root_schema,
                )

        return repaired_obj

    if schema_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [
                _repair_value_by_schema(item, item_schema, root_schema)
                for item in value
            ]

    return _repair_scalar_value(value, schema_type)


_UNREPAIRED = object()


def _repair_union_value(
    value: Any,
    candidates: list[Any],
    root_schema: dict[str, Any],
) -> Any:
    for candidate in candidates:
        if _is_schema_valid(value, candidate, root_schema):
            return value

    for candidate in candidates:
        repaired = _repair_value_by_schema(value, candidate, root_schema)
        if repaired != value and _is_schema_valid(
            repaired,
            candidate,
            root_schema,
        ):
            return repaired

    return _UNREPAIRED


def _is_schema_valid(
    value: Any,
    schema: Any,
    root_schema: dict[str, Any],
) -> bool:
    if isinstance(schema, dict):
        schema = _resolve_schema_ref(schema, root_schema)

    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError:
        return False
    return True


def _resolve_schema_ref(
    schema: dict[str, Any],
    root_schema: dict[str, Any],
) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        return schema

    def_name = ref.removeprefix("#/$defs/")
    def_schema = root_schema.get("$defs", {}).get(def_name)
    if isinstance(def_schema, dict):
        return def_schema

    return schema


def _repair_scalar_value(value: Any, schema_type: Any) -> Any:
    if schema_type == "boolean":
        return _repair_bool(value)

    if schema_type == "integer":
        return _repair_int(value)

    if schema_type == "number":
        return _repair_float(value)

    if schema_type == "string":
        return _repair_string(value)

    return value


def _repair_bool(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False

    if isinstance(value, int) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False

    return value


def _repair_int(value: Any) -> Any:
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    if isinstance(value, str):
        stripped = value.strip()
        try:
            parsed_float = float(stripped)
        except ValueError:
            return value

        if parsed_float.is_integer():
            return int(parsed_float)

    return value


def _repair_float(value: Any) -> Any:
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        try:
            return float(stripped)
        except ValueError:
            return value

    return value


def _repair_string(value: Any) -> Any:
    if isinstance(value, str):
        return value

    if isinstance(value, (bool, int, float)):
        return str(value)

    return value
