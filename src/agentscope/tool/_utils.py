# -*- coding: utf-8 -*-
"""The tool module utils."""
import inspect
import typing
from functools import partial
from typing import Any, Dict, Callable

from docstring_parser import parse
from pydantic import Field, create_model, ConfigDict


def _get_callable_name(tool_func: Callable) -> str:
    """Get the effective name of a callable."""
    named_func = tool_func

    while isinstance(named_func, partial):
        explicit_name = vars(named_func).get("__name__")
        if explicit_name is not None:
            return explicit_name

        named_func = named_func.func

    return getattr(named_func, "__name__", type(named_func).__name__)


def _get_callable_docstring(tool_func: Callable) -> str:
    """Get the effective docstring of a callable.

    An explicitly assigned partial docstring takes precedence. Otherwise,
    unwrap partial objects to avoid using ``functools.partial``'s generic
    documentation.
    """
    documented_func = tool_func

    while isinstance(documented_func, partial):
        if "__doc__" in vars(documented_func):
            return documented_func.__doc__ or ""

        documented_func = documented_func.func

    return documented_func.__doc__ or ""


def _resolve_annotation(
    annotation: Any,
    globalns: dict[str, Any] | None,
) -> Any:
    """Resolve one annotation with the provided global namespace."""

    def _annotation_holder() -> None:
        pass

    _annotation_holder.__annotations__ = {
        "value": annotation,
    }
    return typing.get_type_hints(
        _annotation_holder,
        globalns=globalns,
        include_extras=True,
    )["value"]


def _raw_annotations(obj: Any) -> dict[str, Any]:
    """Return the annotations stored on *obj* without a descriptor lookup.

    On Python 3.14 ``__annotations__`` is a data descriptor on ``object``
    and ``functools.update_wrapper`` copies ``__annotate__`` instead of
    ``__annotations__`` (PEP 749). Collect the wrapped callable's hints
    from ``__annotate__`` / ``__wrapped__``, then let an explicit instance
    ``__dict__`` entry override them.
    """
    merged: dict[str, Any] = {}

    annotate = getattr(obj, "__annotate__", None)
    if callable(annotate):
        try:
            computed = annotate(1)
        except Exception:
            computed = None
        if isinstance(computed, dict):
            merged.update(computed)

    if not merged:
        wrapped_annotations = getattr(
            getattr(obj, "__wrapped__", None),
            "__annotations__",
            None,
        )
        if isinstance(wrapped_annotations, dict):
            merged.update(wrapped_annotations)

    if not merged:
        raw = getattr(obj, "__annotations__", {})
        if isinstance(raw, dict):
            merged.update(raw)

    instance_dict = getattr(obj, "__dict__", None)
    if isinstance(instance_dict, dict):
        entry = instance_dict.get("__annotations__")
        if isinstance(entry, dict):
            merged.update(entry)

    return merged


def _resolve_partial_type_hints(
    partial_func: Callable,
    globalns: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve each annotation on a callable (or partial) independently,
    skipping the ones that cannot be resolved."""
    raw_annotations = _raw_annotations(partial_func)

    type_hints = {}
    for name, annotation in raw_annotations.items():
        try:
            type_hints[name] = _resolve_annotation(
                annotation,
                globalns,
            )
        except Exception:
            continue

    return type_hints


def _resolve_type_hints(tool_func: Callable) -> dict[str, Any]:
    """Resolve and merge annotations from a callable and its partials.

    Annotations are merged from the underlying callable to the outermost
    partial, allowing each partial to override selected annotations.
    """
    partial_funcs = []
    annotated_func = tool_func

    while isinstance(annotated_func, partial):
        partial_funcs.append(annotated_func)
        annotated_func = annotated_func.func

    annotation_sources = [annotated_func]
    if not (
        inspect.isroutine(annotated_func) or inspect.isclass(annotated_func)
    ):
        # Use call hints as defaults, preserving explicit instance hints
        # such as those copied by functools.update_wrapper.
        annotated_func = getattr(annotated_func, "__call__", annotated_func)
        annotation_sources.insert(0, annotated_func)

    globalns = getattr(annotated_func, "__globals__", None)

    type_hints = {}
    for source in annotation_sources:
        source_globals = getattr(
            inspect.unwrap(source),
            "__globals__",
            globalns,
        )
        if not (inspect.isroutine(source) or inspect.isclass(source)):
            # PEP 749: ``get_type_hints`` reads the object-level descriptor
            # on Python 3.14 and misses annotations copied by
            # ``functools.update_wrapper``, so read the instance entry.
            hints = _resolve_partial_type_hints(source, source_globals)
        else:
            try:
                hints = typing.get_type_hints(source, include_extras=True)
            except Exception:
                # An unresolved hint must not discard other valid hints or
                # override hints already resolved from the call method.
                hints = _resolve_partial_type_hints(source, source_globals)
        type_hints.update(hints)

    for partial_func in reversed(partial_funcs):
        type_hints.update(
            _resolve_partial_type_hints(
                partial_func,
                globalns,
            ),
        )

    return type_hints


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


def _build_param_field(description: str | None, default: Any) -> Any:
    """Build the pydantic ``Field`` for a parameter, omitting an absent
    description so it doesn't override metadata carried by
    ``Annotated[..., Field(...)]`` annotations."""
    if description is None:
        return Field(default=default)
    return Field(description=description, default=default)


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
    docstring = parse(_get_callable_docstring(tool_func))
    params_docstring = {_.arg_name: _.description for _ in docstring.params}

    # Under PEP 563 (`from __future__ import annotations`) the annotations
    # in the signature are raw strings, which pydantic cannot resolve
    # outside the function's own module. Resolve them here instead.
    type_hints = _resolve_type_hints(tool_func)

    # Create a dynamic model with the function signature
    fields = {}
    for name, param in inspect.signature(tool_func).parameters.items():
        # Skip `self`, `cls` and the toolkit-injected `_agent_state`
        if name in ["self", "cls", "_agent_state"]:
            continue

        annotation = type_hints.get(name, param.annotation)

        # Handle `**kwargs`
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            if not include_var_keyword:
                continue

            fields[name] = (
                Dict[str, Any]
                if annotation == inspect.Parameter.empty
                else Dict[str, annotation],  # type: ignore
                _build_param_field(
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
                if annotation == inspect.Parameter.empty
                else list[annotation],  # type: ignore
                _build_param_field(
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
                Any if annotation == inspect.Parameter.empty else annotation,
                _build_param_field(
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
