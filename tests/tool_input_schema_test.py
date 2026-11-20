# -*- coding: utf-8 -*-
"""Tests for input-schema extraction of tool functions.

Pydantic rejects model field names starting with an underscore, but such
parameter names are legal Python, and ``FunctionTool`` invokes the wrapped
function with ``**kwargs`` — so the model can still supply them at call
time. Schema extraction must therefore survive them and expose the
original names.
"""
import unittest
from typing import Any

from agentscope.tool import FunctionTool
from agentscope.tool._utils import _extract_input_schema


def search_with_private_arg(_query: str, limit: int = 5) -> str:
    """Search things.

    Args:
        _query (`str`):
            What to look for.
        limit (`int`):
            Max hits.
    """
    return _query * limit


def collision(_query: str, arg_query: int, arg__query: bool = True) -> Any:
    """Params whose sanitized names collide with real params."""
    return (_query, arg_query, arg__query)


def underscore_kwargs(**_kwargs: Any) -> Any:
    """Keyword-collector with an underscore name."""
    return _kwargs


class ExtractInputSchemaTest(unittest.TestCase):
    def test_underscore_param_in_schema(self) -> None:
        schema = _extract_input_schema(search_with_private_arg)
        self.assertEqual(
            set(schema["properties"]),
            {"_query", "limit"},
        )
        self.assertEqual(schema["properties"]["_query"]["type"], "string")
        self.assertEqual(schema["properties"]["_query"]["description"],
                         "What to look for.")
        self.assertEqual(schema["required"], ["_query"])

    def test_sanitized_names_do_not_collide(self) -> None:
        schema = _extract_input_schema(collision)
        self.assertEqual(
            set(schema["properties"]),
            {"_query", "arg_query", "arg__query"},
        )

    def test_underscore_var_keyword(self) -> None:
        schema = _extract_input_schema(
            underscore_kwargs,
            include_var_keyword=True,
        )
        self.assertEqual(set(schema["properties"]), {"_kwargs"})

    def test_function_tool_accepts_underscore_param(self) -> None:
        tool = FunctionTool(search_with_private_arg)
        self.assertEqual(set(tool.input_schema["properties"]),
                         {"_query", "limit"})

        async def run() -> Any:
            chunk = await tool.call(_query="ab", limit=2)
            return chunk

        import asyncio

        result = asyncio.run(run())
        self.assertIn("abab", str(result))


if __name__ == "__main__":
    unittest.main()
