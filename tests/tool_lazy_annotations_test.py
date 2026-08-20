# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Test schema extraction under PEP 563 lazy annotations.

The ``from __future__ import annotations`` import below is the point of
this test module: it turns all annotations into strings, which must be
resolved back to real types before being handed to pydantic.
"""
from __future__ import annotations

from typing import Annotated
from unittest import TestCase

from pydantic import BaseModel, Field

from agentscope.tool import FunctionTool
from agentscope.tool._utils import _extract_input_schema


class Location(BaseModel):
    """A location model used as a parameter type."""

    city: str = Field(description="The city name")


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
