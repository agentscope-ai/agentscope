# -*- coding: utf-8 -*-
"""Regression tests for an explicitly supplied empty tool input schema."""
from unittest import TestCase

from pydantic import BaseModel, Field

from agentscope.tool import FunctionTool


def _no_arg_tool() -> str:
    """Return a fixed value."""
    return "ok"


def _with_arg_tool(value: int) -> int:
    """Return the value."""
    return value


class ExplicitEmptySchemaTest(TestCase):
    """A custom schema must never be silently replaced by inference."""

    def test_empty_dict_schema_is_kept(self) -> None:
        """``input_schema={}`` means "no arguments", not "infer them".

        ``{}`` is a valid, deliberately supplied schema for a no-argument
        tool. Because it is falsy, ``input_schema or _extract_input_schema``
        discards it and regenerates a schema from the function signature.
        """
        tool = FunctionTool(_no_arg_tool, input_schema={})
        self.assertEqual(tool.input_schema, {})

    def test_custom_dict_schema_is_kept(self) -> None:
        """A non-empty custom dict schema is honoured (control)."""
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }
        tool = FunctionTool(_with_arg_tool, input_schema=schema)
        self.assertEqual(tool.input_schema, schema)

    def test_none_still_infers_from_signature(self) -> None:
        """Omitting the schema keeps the inference behaviour (control)."""
        tool = FunctionTool(_with_arg_tool)
        self.assertIn("value", tool.input_schema.get("properties", {}))

    def test_base_model_schema_is_kept(self) -> None:
        """A BaseModel subclass is converted, not re-inferred (control)."""

        class Params(BaseModel):
            """Minimal parameter model used to verify schema conversion."""

            q: str = Field(description="query")

        tool = FunctionTool(_with_arg_tool, input_schema=Params)
        properties = tool.input_schema.get("properties", {})
        self.assertIn("q", properties)
        self.assertNotIn("value", properties)

    def test_empty_schema_does_not_gain_inferred_arguments(self) -> None:
        """Inference must not smuggle signature arguments into ``{}``."""
        tool = FunctionTool(_with_arg_tool, input_schema={})
        self.assertEqual(tool.input_schema, {})
