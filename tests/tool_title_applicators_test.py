# -*- coding: utf-8 -*-
"""Regression tests for title removal inside JSON Schema applicators."""
from typing import Annotated, Any, Union
from unittest import TestCase

from pydantic import Field

from agentscope.tool._utils import (
    _extract_input_schema,
    _remove_title_field,
)


class RemoveTitleApplicatorsTest(TestCase):
    """Titles must be stripped from every nested sub-schema, not only from
    properties/items/additionalProperties/$defs."""

    def setUp(self) -> None:
        self.fn = _remove_title_field

    def test_list_applicators(self) -> None:
        """anyOf/oneOf/allOf/prefixItems nest sub-schemas in a list."""
        for keyword in ("anyOf", "oneOf", "allOf", "prefixItems"):
            with self.subTest(keyword=keyword):
                schema: dict[str, Any] = {
                    keyword: [
                        {"title": "First", "type": "integer"},
                        {"title": "Second", "type": "string"},
                    ],
                }
                self.fn(schema)
                self.assertTrue(
                    all("title" not in branch for branch in schema[keyword]),
                )

    def test_single_schema_keywords(self) -> None:
        """not/if/then/else/contains each hold one sub-schema."""
        for keyword in ("not", "if", "then", "else", "contains"):
            with self.subTest(keyword=keyword):
                schema: dict[str, Any] = {
                    keyword: {"title": "Child", "type": "string"},
                }
                self.fn(schema)
                self.assertNotIn("title", schema[keyword])

    def test_dict_schema_keywords(self) -> None:
        """patternProperties/definitions/dependentSchemas map names to
        sub-schemas."""
        for keyword in (
            "patternProperties",
            "definitions",
            "dependentSchemas",
        ):
            with self.subTest(keyword=keyword):
                schema: dict[str, Any] = {
                    keyword: {
                        "x": {"title": "Child", "type": "string"},
                    },
                }
                self.fn(schema)
                self.assertNotIn("title", schema[keyword]["x"])

    def test_nested_applicators(self) -> None:
        """Applicators can nest, e.g. Optional[Union[...]]."""
        schema: dict[str, Any] = {
            "anyOf": [
                {"anyOf": [{"title": "Inner", "type": "integer"}]},
                {"title": "Null", "type": "null"},
            ],
        }
        self.fn(schema)
        self.assertNotIn("title", schema["anyOf"][0]["anyOf"][0])
        self.assertNotIn("title", schema["anyOf"][1])

    def test_boolean_schema_in_applicator(self) -> None:
        """A boolean schema inside anyOf must be left untouched."""
        schema: dict[str, Any] = {
            "anyOf": [True, {"title": "X", "type": "string"}],
        }
        self.fn(schema)
        self.assertIs(schema["anyOf"][0], True)
        self.assertNotIn("title", schema["anyOf"][1])

    def test_extract_input_schema_strips_union_member_titles(self) -> None:
        """Field(title=...) on Annotated union members survives inside
        pydantic's generated anyOf and would otherwise mislead the LLM."""

        def tool_func(
            value: Union[
                Annotated[int, Field(title="First")],
                Annotated[str, Field(title="Second")],
            ],
        ) -> Union[int, str]:
            """A tool.

            Args:
                value: the value
            """
            return value

        schema = _extract_input_schema(tool_func)
        branches = schema["properties"]["value"].get("anyOf", [])
        self.assertTrue(branches)
        self.assertTrue(all("title" not in branch for branch in branches))
