# -*- coding: utf-8 -*-
"""The tool module utils."""

import inspect
import json
import math
import sys
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Callable

from docstring_parser import parse
from pydantic import Field, create_model, ConfigDict

from .._logging import logger

# Upper bound for integer coercion via Decimal, kept safely below
# Python's int→str limit (default 4300) so json.dumps won't fail later.
_int_str_limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
_MAX_COERCED_INT_DIGITS: int = (
    min(4_000, _int_str_limit - 300) if _int_str_limit else 4_000
)


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


# fmt: off
def _extract_input_schema(
    tool_func: Callable,
    include_var_positional: bool = False,
    include_var_keyword: bool = False,
) -> dict:
    """Extract input schema from the tool function's docstring

    Args:
        tool_func (`Callable`):
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
# fmt: on

    base_model = create_model(
        "_StructuredOutputDynamicClass",
        __config__=ConfigDict(arbitrary_types_allowed=True),
        **fields,
    )
    params_json_schema = base_model.model_json_schema()

    # Remove the title from the json schema
    _remove_title_field(params_json_schema)

    return params_json_schema


def _coerce_tool_args(
    kwargs: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Coerce tool arguments to match the expected JSON schema types.

    This is a best-effort function that attempts to repair type mismatches
    commonly produced by LLMs (e.g. a ``str`` ``"42"`` when an ``int``
    is expected).  When coercion fails for any reason the original value
    is kept silently.

    Args:
        kwargs (`dict[str, Any]`):
            The tool input arguments to coerce.
        schema (`dict[str, Any]`):
            The JSON schema describing the expected parameter types.

    Returns:
        `dict[str, Any]`:
            The coerced arguments.  Keys not present in the schema are
            returned unchanged.
    """
    properties = schema.get("properties", {})
    if not properties or not kwargs:
        return kwargs

    defs = schema.get("$defs", schema.get("definitions", {}))

    result = dict(kwargs)
    for key, value in result.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            continue
        result[key] = _coerce_value(value, prop_schema, defs, param=key)

    return result


def _coerce_value(
    value: Any,
    prop_schema: dict[str, Any],
    defs: dict[str, Any],
    param: str = "?",
) -> Any:
    """Coerce a single value to match a JSON schema property definition.

    Args:
        value (`Any`):
            The value to coerce.
        prop_schema (`dict[str, Any]`):
            The JSON schema for this property (may contain ``$ref``,
            ``anyOf``, ``oneOf``).
        defs (`dict[str, Any]`):
            The ``$defs`` / ``definitions`` block from the parent schema.
        param (`str`):
            The parameter name (for debug logging only).

    Returns:
        `Any`:
            The coerced value, or the original value if coercion fails.
    """
    # Boolean sub-schemas (true / false) are valid JSON Schema but
    # carry no type information — let jsonschema handle them.
    if not isinstance(prop_schema, dict):
        return value

    # Resolve local references before examining the schema.
    resolved_schema = _resolve_alt_ref(prop_schema, defs)
    if resolved_schema is not None:
        prop_schema = resolved_schema

    # Handle anyOf / oneOf (Pydantic's canonical form for Optional[int],
    # int | str, etc.)
    for comb_key in ("anyOf", "oneOf"):
        alternatives = prop_schema.get(comb_key)
        if isinstance(alternatives, list):
            return _coerce_composite(
                value,
                alternatives,
                defs,
                param,
            )

    expected_type = prop_schema.get("type")

    # No type constraint — still recurse into nested structures
    if expected_type is None:
        return _coerce_nested(value, prop_schema, defs, param)

    # Union types: ["string", "null"], ["integer", "string"], etc.
    if isinstance(expected_type, list):
        return _coerce_union(value, expected_type, prop_schema, defs, param)

    # Single type
    coerced = _coerce_to_type(value, expected_type)
    if coerced is not value:
        _log_coercion(param, type(value).__name__, expected_type)

    return _coerce_nested(coerced, prop_schema, defs, param)


def _coerce_composite(
    value: Any,
    alternatives: list[dict[str, Any]],
    defs: dict[str, Any],
    param: str,
) -> Any:
    """Coerce against an ``anyOf`` / ``oneOf`` alternative list.

    Selects an unambiguous type-matching alternative first. If the value
    matches no alternative type, it makes a best-effort coercion attempt.

    Args:
        value (`Any`):
            The value to coerce.
        alternatives (`list[dict]`):
            The ``anyOf`` / ``oneOf`` list from the schema.
        defs (`dict[str, Any]`):
            The ``$defs`` block for resolving ``$ref``.
        param (`str`):
            The parameter name for logging.

    Returns:
        `Any`:
            The coerced value.
    """
    # Collect all type-matching alternatives (for discriminated unions we
    # need the best match, not just the first).
    type_matches: list[tuple[dict[str, Any], int]] = []
    any_type_match = False
    for alt in alternatives:
        resolved_alt = _resolve_alt_ref(alt, defs)
        if resolved_alt is None or not isinstance(resolved_alt, dict):
            continue
        alt_type = resolved_alt.get("type")
        if alt_type == "null" and value is None:
            return _coerce_value(value, resolved_alt, defs, param)
        if isinstance(alt_type, str) and _value_matches_type(value, alt_type):
            any_type_match = True
            score = _discriminator_score(value, resolved_alt)
            if score >= 0:
                type_matches.append((resolved_alt, score))

    if type_matches:
        type_matches.sort(key=lambda x: x[1], reverse=True)
        best_alt, best_score = type_matches[0]
        if len(type_matches) > 1 and type_matches[1][1] == best_score:
            return value
        return _coerce_value(value, best_alt, defs, param)

    # If the value matched the type of some alternatives but every one
    # was disqualified by discriminator/required, don't fall through to
    # the coercion loop — it would blindly use the first alt's schema.
    if any_type_match and isinstance(value, dict):
        return value

    # Try coercion against each non-null alternative
    for alt in alternatives:
        resolved_alt = _resolve_alt_ref(alt, defs)
        if resolved_alt is None or not isinstance(resolved_alt, dict):
            continue
        alt_type = resolved_alt.get("type")
        if alt_type == "null" or not isinstance(alt_type, str):
            continue
        try:
            coerced = _coerce_to_type(value, alt_type)
        except (ValueError, TypeError):
            continue
        if _value_matches_type(coerced, alt_type):
            if coerced is not value:
                _log_coercion(param, type(value).__name__, alt_type)
            return _coerce_value(coerced, resolved_alt, defs, param)

    return value


def _discriminator_score(value: Any, schema: dict[str, Any]) -> int:
    """Evaluate a discriminated union branch against a value.

    Returns a non-negative score when the branch is a viable match
    (higher is better).  Returns ``-1`` when the branch is
    **disqualified** — any ``const`` field contradicts the input, or a
    ``required`` field is missing.

    Args:
        value (`Any`):
            The value to check (typically a ``dict``).
        schema (`dict[str, Any]`):
            A resolved alternative schema.

    Returns:
        `int`:
            Discrimination score (≥ 0), or ``-1`` if disqualified.
    """
    if not isinstance(value, dict):
        return 0
    props = schema.get("properties")
    if not isinstance(props, dict):
        return 0
    # const contradiction → disqualify
    for key, prop_schema in props.items():
        if "const" in prop_schema:
            if key not in value or value[key] != prop_schema["const"]:
                return -1
    # missing required field → disqualify
    for key in schema.get("required", []):
        if key not in value:
            return -1
    # Viable: score by how many required fields are present
    score = 0
    for key in schema.get("required", []):
        score += 1  # each is present (already verified above)
    for key, prop_schema in props.items():
        if "const" in prop_schema:
            score += 10
    return score


def _resolve_alt_ref(
    alt: dict[str, Any],
    defs: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve a ``$ref`` reference in an anyOf/oneOf alternative.

    Args:
        alt (`dict`):
            An alternative schema, possibly containing ``$ref``.
        defs (`dict`):
            The ``$defs`` block.

    Returns:
        `dict | None`:
            The resolved schema, or ``None`` if unresolvable.
    """
    if not isinstance(alt, dict):
        return alt
    if "$ref" not in alt:
        return alt
    ref_name = alt["$ref"].split("/")[-1]
    return defs.get(ref_name)


def _coerce_nested(
    value: Any,
    prop_schema: dict[str, Any],
    defs: dict[str, Any],
    param: str = "?",
) -> Any:
    """Recursively coerce nested structures (array items, object properties).

    Args:
        value (`Any`):
            The value whose nested contents to coerce.
        prop_schema (`dict[str, Any]`):
            The JSON schema for this value.
        defs (`dict[str, Any]`):
            The ``$defs`` block for resolving ``$ref``.
        param (`str`):
            The parameter name for logging context.

    Returns:
        `Any`:
            The value with nested contents coerced.
    """
    # Coerce array items
    if isinstance(value, list) and isinstance(prop_schema.get("items"), dict):
        item_schema = prop_schema["items"]
        return [
            _coerce_value(
                item,
                item_schema,
                defs,
                param=f"{param}[{i}]",
            )
            for i, item in enumerate(value)
        ]

    # Coerce object properties recursively
    if isinstance(value, dict):
        result = dict(value)
        # Handle known properties
        props = prop_schema.get("properties")
        if isinstance(props, dict):
            virtual_schema = {"properties": props, "$defs": defs}
            result = _coerce_tool_args(result, virtual_schema)
        # Handle additionalProperties (e.g. dict[str, int])
        addl = prop_schema.get("additionalProperties")
        if isinstance(addl, dict):
            known = set(props.keys()) if isinstance(props, dict) else set()
            for key, val in result.items():
                if key not in known:
                    result[key] = _coerce_value(
                        val,
                        addl,
                        defs,
                        param=f"{param}.{key}",
                    )
        return result

    return value


def _log_coercion(param: str, from_type: str, to_schema_type: str) -> None:
    """Log a type coercion event without exposing parameter values.

    Args:
        param (`str`):
            The parameter name.
        from_type (`str`):
            The original Python type name.
        to_schema_type (`str`):
            The target JSON Schema type.
    """
    logger.debug(
        "Coerced tool arg '%s' from %s to match schema type %s",
        param,
        from_type,
        to_schema_type,
    )


def _coerce_union(
    value: Any,
    types: list[str],
    prop_schema: dict[str, Any],
    defs: dict[str, Any],
    param: str,
) -> Any:
    """Coerce a value against a ``type`` array (e.g. ``["string", "null"]``).

    Tries each type in order; returns the first successful coercion.

    Args:
        value (`Any`):
            The value to coerce.
        types (`list[str]`):
            The allowed types.
        prop_schema (`dict[str, Any]`):
            The JSON schema for this property.
        defs (`dict[str, Any]`):
            The ``$defs`` block for resolving ``$ref``.
        param (`str`):
            The parameter name for logging.

    Returns:
        `Any`:
            The coerced value.
    """
    # If the value already matches one of the types, just recurse
    for t in types:
        if t == "null" and value is None:
            return _coerce_nested(value, prop_schema, defs, param)
        if _value_matches_type(value, t):
            return _coerce_nested(value, prop_schema, defs, param)

    # Try coercion to each non-null type
    for t in types:
        if t == "null":
            continue
        try:
            coerced = _coerce_to_type(value, t)
        except (ValueError, TypeError):
            continue
        if _value_matches_type(coerced, t):
            if coerced is not value:
                _log_coercion(param, type(value).__name__, t)
            return _coerce_nested(coerced, prop_schema, defs, param)

    # Nothing worked — return as-is
    return value


def _value_matches_type(value: Any, expected_type: str) -> bool:
    """Check whether a value's Python type matches a JSON schema type.

    Args:
        value (`Any`):
            The value to check.
        expected_type (`str`):
            The JSON schema type name.

    Returns:
        `bool`:
            ``True`` if the value matches the expected type.
    """
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(
            value,
            bool,
        )
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "null":
        return value is None
    return False


# pylint: disable=too-many-return-statements,too-many-branches
def _coerce_to_type(value: Any, expected_type: str) -> Any:
    """Best-effort, lossless coercion of a single value to the expected
    JSON Schema type.

    Only applies when the coercion is unambiguous and preserves the
    intended value.  Ambiguous or lossy conversions (e.g. ``"42.9"`` →
    ``int``) are left unchanged so the original validation / tool error
    surfaces.

    Args:
        value (`Any`):
            The value to coerce.
        expected_type (`str`):
            The target JSON schema type.

    Returns:
        `Any`:
            The coerced value, or the original value if coercion is not
            applicable or fails.
    """
    if expected_type == "string":
        if not isinstance(value, str):
            return str(value)
        return value

    if expected_type == "integer":
        if isinstance(value, bool):
            # bool is a subclass of int — keep it as-is
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            # inf / nan cannot be coerced to int
            if not math.isfinite(value):
                return value
            # Only coerce if the float represents an exact integer
            try:
                as_int = int(value)
                if value == as_int:
                    return as_int
            except (ValueError, OverflowError):
                pass
            return value
        if isinstance(value, str):
            stripped = value.strip()
            # Try direct int parsing first — handles plain integer
            # strings without any precision loss.
            try:
                return int(stripped)
            except (ValueError, TypeError):
                pass
            # Fall back via Decimal: preserves precision for strings
            # like "1e23" or "100000000000000000000000.0" that float()
            # would silently corrupt.
            try:
                d = Decimal(stripped)
                if d.is_finite() and d == d.to_integral_value():
                    # d.adjusted() estimates digit count; skip huge
                    # values to avoid OOM (e.g. "1e1000000000").
                    if d.adjusted() < _MAX_COERCED_INT_DIGITS:
                        return int(d)
            except (ValueError, TypeError, InvalidOperation, OverflowError):
                pass
        return value

    if expected_type == "number":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            # JSON Schema "number" accepts integers — no coercion needed
            return value
        if isinstance(value, str):
            stripped = value.strip()
            try:
                parsed = float(stripped)
                if math.isfinite(parsed):
                    return parsed
            except (ValueError, TypeError):
                pass
        return value

    if expected_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            stripped = value.strip().lower()
            if stripped in ("true", "1", "yes"):
                return True
            if stripped in ("false", "0", "no"):
                return False
        if isinstance(value, int):
            if value in (0, 1):
                return bool(value)
        if isinstance(value, float):
            if value in (0.0, 1.0):
                return bool(value)
        return value

    if expected_type == "array":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        # Wrap single value in a list
        return [value]

    if expected_type == "object":
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return value

    return value
