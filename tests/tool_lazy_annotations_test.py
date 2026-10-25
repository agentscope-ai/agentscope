# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Test schema extraction under PEP 563 lazy annotations.

The ``from __future__ import annotations`` import below is the point of
this test module: it turns all annotations into strings, which must be
resolved back to real types before being handed to pydantic.
"""
from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Annotated, Literal
from unittest import TestCase

from pydantic import BaseModel, Field

from agentscope.tool import FunctionTool
from agentscope.tool._utils import _extract_input_schema

if TYPE_CHECKING:
    from agentscope.state import AgentState


def state_injected_tool(
    query: Annotated[str, Field(description="The search query")],
    mode: Literal["web", "news"] = "web",
    _agent_state: AgentState | None = None,
) -> str:
    """Search for something."""
    return query


class Location(BaseModel):
    """A location model used as a parameter type."""

    city: str = Field(description="The city name")


class SearchQuery(BaseModel):
    """A structured catalog-search query."""

    text: str = Field(description="The query text")


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


class LazyAnnotationsTest(TestCase):
    """Schema extraction must work with stringized annotations."""

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

    def test_unresolvable_annotation_does_not_discard_the_others(
        self,
    ) -> None:
        """A type imported only under TYPE_CHECKING must not discard the other hints."""
        schema = _extract_input_schema(state_injected_tool)

        self.assertDictEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["web", "news"],
                        "default": "web",
                    },
                },
                "required": ["query"],
            },
        )
