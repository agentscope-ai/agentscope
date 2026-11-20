# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Test schema extraction under PEP 563 lazy annotations.

The ``from __future__ import annotations`` import below is the point of
this test module: it turns all annotations into strings, which must be
resolved back to real types before being handed to pydantic.
"""
from __future__ import annotations

from functools import lru_cache, partial, update_wrapper
from typing import TYPE_CHECKING, Annotated, Any, Callable, Literal
from unittest import TestCase

from pydantic import BaseModel, Field

from agentscope.tool import FunctionTool
from agentscope.tool._utils import _extract_input_schema

if TYPE_CHECKING:
    from agentscope.state import AgentState


class Location(BaseModel):
    """A location model used as a parameter type."""

    city: str = Field(description="The city name")


class SearchQuery(BaseModel):
    """A structured catalog-search query."""

    text: str = Field(description="The query text")


class CallableTool:
    """A callable instance with lazy annotations."""

    def __call__(
        self,
        mode: Literal["fast", "full"],
        limit: int = 5,
    ) -> str:
        return mode


class StateInjectedCallableTool:
    """A callable with an annotation unavailable at runtime."""

    def __call__(
        self,
        mode: Literal["fast", "full"],
        _agent_state: AgentState | None = None,
    ) -> str:
        return mode


class CallableWrapper:
    """Preserve a wrapped callable's more specific annotations."""

    def __init__(self, func: Callable[..., str]) -> None:
        self.__annotations__: dict[str, Any] = {}
        update_wrapper(self, func)
        self.func = func

    def __call__(self, mode: Any, limit: int = 5) -> str:
        return self.func(mode, limit)


class DocumentedCallableTool:
    """A generic description of the class, not of the tool call."""

    def __call__(self, mode: Literal["fast", "full"]) -> str:
        """Search with a mode switch.

        Args:
            mode: The search mode to use.
        """
        return mode


def wrapped_tool(catalog: str, query: str) -> str:
    """Search the selected catalog.

    Args:
        catalog: The catalog identifier.
        query: The query to run.
    """
    return f"{catalog}:{query}"


MODE_SCHEMA = {
    "type": "object",
    "properties": {"mode": {"type": "string", "enum": ["fast", "full"]}},
    "required": ["mode"],
}

DOCUMENTED_MODE_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["fast", "full"],
            "description": "The search mode to use.",
        },
    },
    "required": ["mode"],
}

CATALOG_SCHEMA = {
    "type": "object",
    "properties": {
        "catalog": {
            "type": "string",
            "description": "The catalog identifier.",
        },
        "query": {"type": "string", "description": "The query to run."},
    },
    "required": ["catalog", "query"],
}


@lru_cache(maxsize=8)
def cached_tool(catalog: str, query: str) -> str:
    """Search the cached catalog.

    Args:
        catalog: The catalog identifier.
        query: The query to run.
    """
    return f"{catalog}:{query}"


def annotated_tool(
    query: Annotated[str, Field(description="The search query")],
    limit: int = 5,
) -> str:
    """Search for something."""
    return query


def kwargs_tool(**kwargs: Annotated[str, Field(description="Extras")]) -> None:
    """A tool receiving keyword arguments only."""


def model_tool(location: Location) -> str:
    """Report the weather.

    Args:
        location: Where to report the weather for.
    """
    return location.city


def catalog_search(
    catalog: str,
    query: Annotated[SearchQuery, Field(description="The search query")],
    limit: Annotated[int, Field(gt=0, description="Maximum results")] = 5,
) -> str:
    """Search a catalog."""
    return f"{catalog}:{query.text}:{limit}"


def documented_search(catalog: str, query: str) -> str:
    """Search the selected catalog.

    Args:
        catalog: The catalog identifier.
        query: The query to run.
    """
    return f"{catalog}:{query}"


def primitive_parameter_with_unresolvable_return(value: int) -> str:
    """Keep the parameter annotation usable when the return hint is not."""
    return str(value)


primitive_parameter_with_unresolvable_return.__annotations__ = {
    "value": int,
    "return": "NotDefined",
}


def state_injected_tool(
    query: Annotated[str, Field(description="The search query")],
    _agent_state: AgentState | None = None,
) -> str:
    """Search with the injected agent state."""
    return query


class LazyAnnotationsTest(TestCase):
    """Schema extraction must work with stringized annotations."""

    def test_callable_wrapper_preserves_instance_annotations(self) -> None:
        """Instance hints take precedence over the wrapper's call hints."""
        wrapped = CallableWrapper(CallableTool().__call__)

        self.assertDictEqual(
            FunctionTool(wrapped).input_schema,
            {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["fast", "full"]},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["mode"],
            },
        )

    def test_callable_wrapper_keeps_valid_call_hints(self) -> None:
        """An unresolved instance hint does not replace a valid call hint."""
        wrapped = CallableWrapper(CallableTool().__call__)
        wrapped.__annotations__ = {
            **wrapped.__annotations__,
            "limit": "NotDefined",
        }

        self.assertDictEqual(
            FunctionTool(wrapped).input_schema,
            {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["fast", "full"]},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["mode"],
            },
        )

    def test_callable_instance_resolves_lazy_annotations(self) -> None:
        """Callable instances use their bound call method's hints."""
        schema = FunctionTool(CallableTool()).input_schema

        self.assertDictEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["fast", "full"]},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["mode"],
            },
        )

    def test_partial_callable_instance_resolves_lazy_annotations(self) -> None:
        """Partials retain defaults and resolve overrides in method globals."""
        tool_func = partial(CallableTool(), limit=3)
        tool_func.__annotations__ = {"limit": "Annotated[int, Field(gt=0)]"}
        schema = FunctionTool(tool_func).input_schema

        self.assertDictEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["fast", "full"]},
                    "limit": {
                        "type": "integer",
                        "default": 3,
                        "exclusiveMinimum": 0,
                    },
                },
                "required": ["mode"],
            },
        )

    def test_callable_instance_keeps_resolvable_hints(self) -> None:
        """An unavailable state hint does not discard the method's hints."""
        tool = FunctionTool(
            StateInjectedCallableTool(),
            is_state_injected=True,
        )

        self.assertDictEqual(
            tool.input_schema,
            {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["fast", "full"]},
                },
                "required": ["mode"],
            },
        )

    def test_annotated_field_parameter(self) -> None:
        """`Annotated[str, Field(...)]` params must be resolved."""
        schema = _extract_input_schema(annotated_tool)
        self.assertDictEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )

    def test_annotated_var_keyword(self) -> None:
        """`**kwargs` annotations must be resolved as well."""
        schema = _extract_input_schema(
            kwargs_tool,
            include_var_keyword=True,
        )
        self.assertDictEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "kwargs": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "string",
                            "description": "Extras",
                        },
                        "default": {},
                    },
                },
            },
        )

    def test_pydantic_model_parameter(self) -> None:
        """Custom model types must be resolved from the defining module."""
        schema = _extract_input_schema(model_tool)
        self.assertDictEqual(
            schema,
            {
                "type": "object",
                "$defs": {
                    "Location": {
                        "type": "object",
                        "description": (
                            "A location model used as a parameter type."
                        ),
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "The city name",
                            },
                        },
                        "required": ["city"],
                    },
                },
                "properties": {
                    "location": {
                        "$ref": "#/$defs/Location",
                        "description": "Where to report the weather for.",
                    },
                },
                "required": ["location"],
            },
        )

    def test_function_tool_registration(self) -> None:
        """FunctionTool must expose the resolved schema end to end."""
        tool = FunctionTool(annotated_tool)
        self.assertDictEqual(
            tool.input_schema,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )

    def test_positional_partial_resolves_lazy_model_annotations(self) -> None:
        """A partial keeps its signature while using its function's hints."""
        tool_func = partial(catalog_search, "catalog-a")
        expected = {
            "type": "object",
            "$defs": {
                "SearchQuery": {
                    "type": "object",
                    "description": "A structured catalog-search query.",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The query text",
                        },
                    },
                    "required": ["text"],
                },
            },
            "properties": {
                "query": {
                    "$ref": "#/$defs/SearchQuery",
                    "description": "The search query",
                },
                "limit": {
                    "type": "integer",
                    "exclusiveMinimum": 0,
                    "description": "Maximum results",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

        self.assertDictEqual(_extract_input_schema(tool_func), expected)
        self.assertDictEqual(
            FunctionTool(tool_func, name="catalog_search").input_schema,
            expected,
        )

    def test_nested_partial_uses_remaining_keyword_default(self) -> None:
        """Nested partials retain the signature-provided default."""
        tool_func = partial(partial(catalog_search, "catalog-a"), limit=3)
        schema = _extract_input_schema(tool_func)

        self.assertEqual(schema["required"], ["query"])
        self.assertEqual(schema["properties"]["limit"]["default"], 3)
        self.assertEqual(
            schema["properties"]["query"]["$ref"],
            "#/$defs/SearchQuery",
        )

    def test_partial_uses_its_own_annotations_before_underlying_function(
        self,
    ) -> None:
        """Explicit annotations on a partial override its function hints."""
        tool_func = partial(catalog_search, "catalog-a")
        tool_func.__annotations__ = {"query": str}

        self.assertDictEqual(
            _extract_input_schema(tool_func),
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "exclusiveMinimum": 0,
                        "description": "Maximum results",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )

    def test_partial_keeps_valid_annotation_override(self) -> None:
        """An invalid annotation does not discard valid overrides."""
        tool_func = partial(catalog_search, "catalog-a")
        tool_func.__annotations__ = {
            "query": str,
            "limit": "TotallyUnresolvable",
        }

        self.assertDictEqual(
            _extract_input_schema(tool_func),
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "exclusiveMinimum": 0,
                        "description": "Maximum results",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )

    def test_unresolvable_partial_annotation_keeps_function_hint(
        self,
    ) -> None:
        """An invalid partial annotation does not replace valid hints."""
        tool_func = partial(catalog_search, "catalog-a")
        tool_func.__annotations__ = {
            "query": "TotallyUnresolvable",
        }

        self.assertDictEqual(
            _extract_input_schema(tool_func),
            {
                "type": "object",
                "$defs": {
                    "SearchQuery": {
                        "type": "object",
                        "description": "A structured catalog-search query.",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "The query text",
                            },
                        },
                        "required": ["text"],
                    },
                },
                "properties": {
                    "query": {
                        "$ref": "#/$defs/SearchQuery",
                        "description": "The search query",
                    },
                    "limit": {
                        "type": "integer",
                        "exclusiveMinimum": 0,
                        "description": "Maximum results",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )

    def test_partial_uses_underlying_function_name(self) -> None:
        """A partial inherits its default tool name."""
        tool = FunctionTool(
            partial(documented_search, "catalog-a"),
        )

        self.assertEqual(tool.name, "documented_search")

    def test_partial_uses_underlying_function_docstring(self) -> None:
        """A partial inherits tool and parameter documentation."""
        tool = FunctionTool(
            partial(documented_search, "catalog-a"),
            name="documented_search",
        )

        self.assertEqual(
            tool.description,
            "Search the selected catalog.",
        )
        self.assertDictEqual(
            tool.input_schema,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The query to run.",
                    },
                },
                "required": ["query"],
            },
        )

    def test_partial_prefers_its_explicit_docstring(self) -> None:
        """An explicitly assigned partial docstring takes precedence."""
        tool_func = partial(documented_search, "catalog-a")
        tool_func.__doc__ = """Search a custom catalog.

        Args:
            query: The custom query to run.
        """
        tool = FunctionTool(
            tool_func,
            name="documented_search",
        )

        self.assertEqual(
            tool.description,
            "Search a custom catalog.",
        )
        self.assertDictEqual(
            tool.input_schema,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The custom query to run.",
                    },
                },
                "required": ["query"],
            },
        )

    def test_partial_falls_back_to_usable_raw_parameter_annotation(
        self,
    ) -> None:
        """An unresolvable return hint does not discard a usable parameter."""
        schema = _extract_input_schema(
            partial(primitive_parameter_with_unresolvable_return),
        )

        self.assertDictEqual(
            schema,
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        )

    def test_type_checking_only_annotation_keeps_other_hints(self) -> None:
        """A ``TYPE_CHECKING``-only annotation on the injected state does
        not discard the hints of the other parameters."""
        tool = FunctionTool(state_injected_tool, is_state_injected=True)

        self.assertDictEqual(
            tool.input_schema,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                },
                "required": ["query"],
            },
        )

    def test_callable_object_uses_call_docstring(self) -> None:
        """A callable instance describes its call via `__call__`'s
        docstring, matching what a plain function would provide."""
        tool = FunctionTool(DocumentedCallableTool())

        self.assertEqual(
            (tool.description, tool.input_schema),
            ("Search with a mode switch.", DOCUMENTED_MODE_SCHEMA),
        )

    def test_explicit_instance_docstring_takes_precedence(self) -> None:
        """A docstring set on the instance itself wins over `__call__`'s."""
        tool_fn = DocumentedCallableTool()
        tool_fn.__doc__ = "Explicit tool doc."
        tool = FunctionTool(tool_fn)

        self.assertEqual(
            (tool.description, tool.input_schema),
            ("Explicit tool doc.", MODE_SCHEMA),
        )

    def test_update_wrapper_docstring_preserved(self) -> None:
        """A docstring copied onto the instance by update_wrapper is
        used for the tool description and parameter docs."""
        tool = FunctionTool(CallableWrapper(wrapped_tool))

        self.assertEqual(
            (tool.description, tool.input_schema),
            ("Search the selected catalog.", CATALOG_SCHEMA),
        )

    def test_slotted_docstring_takes_precedence_when_assigned(self) -> None:
        """A doc slot overrides the call docs only after assignment."""

        class SlottedTool:
            __slots__ = ("__doc__",)
            __call__ = DocumentedCallableTool.__call__

        func = SlottedTool()
        tool = FunctionTool(func)
        self.assertEqual(
            (tool.description, tool.input_schema),
            ("Search with a mode switch.", DOCUMENTED_MODE_SCHEMA),
        )
        for doc in ("Tenant-specific search.", None):
            with self.subTest(doc=doc):
                func.__doc__ = doc
                tool = FunctionTool(func)
                self.assertEqual(
                    (tool.description, tool.input_schema),
                    (doc or "", MODE_SCHEMA),
                )

    def test_update_wrapper_preserves_absent_docstring(self) -> None:
        """An undocumented wrapped function has no wrapper call docs."""

        def undocumented(mode: str) -> str:
            return mode

        wrapped = update_wrapper(DocumentedCallableTool(), undocumented)
        tool = FunctionTool(wrapped)
        self.assertEqual(
            (tool.description, tool.input_schema),
            (
                "",
                {
                    "type": "object",
                    "properties": {"mode": {"type": "string"}},
                    "required": ["mode"],
                },
            ),
        )

    def test_lru_cache_docstring_preserved(self) -> None:
        """An lru_cache-wrapped tool keeps its docstring-derived
        descriptions."""
        tool = FunctionTool(cached_tool)

        self.assertEqual(
            (tool.description, tool.input_schema),
            ("Search the cached catalog.", CATALOG_SCHEMA),
        )
