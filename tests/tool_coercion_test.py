# -*- coding: utf-8 -*-
"""Regression tests for schema-driven ToolBase argument coercion."""

import math
import unittest
from typing import Any, Optional

import jsonschema
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.tool import (
    FunctionTool,
    ToolBase,
    ToolChunk,
    Toolkit,
    ToolMiddlewareBase,
)
from agentscope.tool._utils import _coerce_tool_args


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal object schema for a test case."""
    return {"type": "object", "properties": properties}


class ToolCoercionTest(unittest.TestCase):
    """Unit tests grouped by coercion behavior."""

    def test_scalar_coercion(self) -> None:
        """Repair common scalar mismatches without changing valid numbers."""
        cases = (
            ("integer", "42", 42, int),
            ("number", "3.14", 3.14, float),
            ("number", 42, 42, int),
            ("boolean", "TRUE", True, bool),
            ("boolean", "0", False, bool),
            ("string", 42, "42", str),
            ("integer", 42.0, 42, int),
        )
        for expected_type, value, expected, python_type in cases:
            with self.subTest(expected_type=expected_type, value=value):
                result = _coerce_tool_args(
                    {"value": value},
                    _schema({"value": {"type": expected_type}}),
                )
                self.assertEqual(result["value"], expected)
                self.assertIsInstance(result["value"], python_type)

    def test_string_coercion_preserves_non_scalar_values(self) -> None:
        """Only scalar values are coerced to strings."""
        schema = _schema({"value": {"type": "string"}})
        for value in ({"key": "value"}, ["value"], None, b"value"):
            with self.subTest(value=value):
                result = _coerce_tool_args({"value": value}, schema)
                self.assertEqual(result["value"], value)

        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                result = _coerce_tool_args({"value": value}, schema)
                self.assertIs(result["value"], value)

    def test_integer_coercion_is_lossless_and_safe(self) -> None:
        """Avoid truncation, precision loss, and exceptions."""
        schema = _schema({"value": {"type": "integer"}})
        exact_cases = {
            "9007199254740993": 9007199254740993,
            "1e23": 100000000000000000000000,
            "100000000000000000000000.0": 100000000000000000000000,
        }
        for value, expected in exact_cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    _coerce_tool_args({"value": value}, schema)["value"],
                    expected,
                )

        unchanged = (
            "42.9",
            "not_a_number",
            "Infinity",
            "-Infinity",
            42.9,
        )
        for value in unchanged:
            with self.subTest(value=value):
                self.assertEqual(
                    _coerce_tool_args({"value": value}, schema)["value"],
                    value,
                )

        for value, check in (
            (float("inf"), math.isinf),
            (float("nan"), math.isnan),
        ):
            with self.subTest(value=value):
                self.assertTrue(
                    check(
                        _coerce_tool_args({"value": value}, schema)["value"],
                    ),
                )

    def test_integer_coercion_guards_against_huge_values(self) -> None:
        """Huge values via scientific notation are left unchanged."""
        schema = _schema({"value": {"type": "integer"}})
        # Should be kept as-is — would produce a billion-digit int
        self.assertEqual(
            _coerce_tool_args({"value": "1e1000000000"}, schema)["value"],
            "1e1000000000",
        )
        # Should be kept as-is — exceeds the digit limit
        self.assertEqual(
            _coerce_tool_args({"value": "1e10000"}, schema)["value"],
            "1e10000",
        )
        # Within the limit — should be coerced
        self.assertEqual(
            _coerce_tool_args({"value": "1e23"}, schema)["value"],
            100000000000000000000000,
        )

    def test_nested_and_collection_coercion(self) -> None:
        """Recurse through objects, arrays, JSON values, and refs."""
        schema = _schema(
            {
                "items": {"type": "array", "items": {"type": "integer"}},
                "config": {
                    "type": "object",
                    "properties": {"port": {"type": "integer"}},
                },
                "ref": {"$ref": "#/$defs/Config"},
            },
        )
        schema["$defs"] = {
            "Config": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
            },
        }
        result = _coerce_tool_args(
            {
                "items": '["1", "2"]',
                "config": '{"port": "8080"}',
                "ref": {"id": "123"},
            },
            schema,
        )
        self.assertEqual(
            result,
            {"items": [1, 2], "config": {"port": 8080}, "ref": {"id": 123}},
        )

        singleton = _coerce_tool_args(
            {"items": "1"},
            _schema(
                {"items": {"type": "array", "items": {"type": "integer"}}},
            ),
        )
        self.assertEqual(singleton["items"], [1])

    def test_composite_schema_coercion(self) -> None:
        """Handle Pydantic-style anyOf/oneOf schemas."""
        schema = _schema(
            {
                "optional": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                },
                "union": {
                    "anyOf": [{"type": "integer"}, {"type": "string"}],
                },
                "one_of": {
                    "oneOf": [{"type": "integer"}, {"type": "string"}],
                },
                "numbers": {
                    "anyOf": [
                        {"type": "array", "items": {"type": "integer"}},
                        {"type": "null"},
                    ],
                },
            },
        )
        result = _coerce_tool_args(
            {
                "optional": "10",
                "union": "42",
                "one_of": "100",
                "numbers": ["1", "2"],
            },
            schema,
        )
        self.assertEqual(
            result,
            {
                "optional": 10,
                "union": "42",
                "one_of": "100",
                "numbers": [1, 2],
            },
        )
        self.assertIsNone(
            _coerce_tool_args({"optional": None}, schema)["optional"],
        )

    def test_anyof_coercion_continues_on_failure(self) -> None:
        """When a coercion attempt fails, the loop continues to the next
        alternative instead of stopping at the first one."""
        schema = _schema(
            {
                "value": {
                    "anyOf": [{"type": "integer"}, {"type": "number"}],
                },
            },
        )
        # "3.14" can't be integer, but can be coerced to number (3.14)
        result = _coerce_tool_args({"value": "3.14"}, schema)
        self.assertIsInstance(
            result["value"],
            float,
            "Should coerce to float, not keep str",
        )
        self.assertEqual(result["value"], 3.14)

    def test_composite_coercion_preserves_valid_values(self) -> None:
        """Coercion does not invalidate an already-valid oneOf value."""
        schema = _schema(
            {
                "value": {
                    "oneOf": [True, {"type": "integer"}],
                },
            },
        )
        result = _coerce_tool_args({"value": "42"}, schema)

        self.assertEqual(result, {"value": "42"})
        jsonschema.validate(result, schema)

    def test_composite_coercion_keeps_root_reference_context(self) -> None:
        """Composite schemas retain access to root definitions."""
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "$defs": {"LocalType": {"type": "null"}},
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {
                                "x": {"$ref": "#/$defs/RootType"},
                            },
                        },
                    ],
                },
            },
            "$defs": {"RootType": {"type": "integer"}},
        }

        result = _coerce_tool_args({"value": {"x": "42"}}, schema)

        self.assertEqual(result, {"value": {"x": 42}})
        jsonschema.validate(result, schema)

    def test_discriminated_unions_are_conservative(self) -> None:
        """Select matching branch, leave unknown branches intact."""
        schema = _schema(
            {
                "item": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"const": "a"},
                                "x": {"type": "integer"},
                                "extra": {"type": "integer"},
                            },
                            "required": ["kind", "x", "extra"],
                        },
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"const": "b"},
                                "y": {"type": "integer"},
                            },
                            "required": ["kind", "y"],
                        },
                    ],
                },
            },
        )
        matched = _coerce_tool_args(
            {"item": {"kind": "b", "y": "42"}},
            schema,
        )
        self.assertEqual(matched["item"], {"kind": "b", "y": 42})
        unknown = {"kind": "c", "x": "1", "extra": "2"}
        self.assertEqual(
            _coerce_tool_args({"item": unknown}, schema)["item"],
            unknown,
        )

    def test_dynamic_maps_and_passthrough_are_preserved(self) -> None:
        """Coerce additionalProperties, preserve unknown top-level keys."""
        schema = _schema(
            {
                "counts": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                "name": {"type": "string"},
            },
        )
        result = _coerce_tool_args(
            {
                "counts": {"retries": "3", "timeout": "30"},
                "name": "ok",
                "extra": 42,
            },
            schema,
        )
        self.assertEqual(
            result,
            {
                "counts": {"retries": 3, "timeout": 30},
                "name": "ok",
                "extra": 42,
            },
        )

    def test_passthrough_and_idempotency(self) -> None:
        """No-op inputs and repeated coercion should be stable."""
        schema = _schema(
            {
                "count": {"type": "integer"},
                "name": {"type": "string"},
                "flag": {"type": "boolean"},
            },
        )
        value = {"count": "42", "name": "hello", "flag": True}
        once = _coerce_tool_args(value, schema)
        self.assertEqual(_coerce_tool_args(once, schema), once)
        self.assertEqual(_coerce_tool_args({}, schema), {})
        self.assertEqual(
            _coerce_tool_args({"x": "1"}, {"type": "object"}),
            {"x": "1"},
        )

    def test_boolean_only_coerces_zero_and_one(self) -> None:
        """Only exact 0/1 are coerced to bool; other numbers stay unchanged."""
        schema = _schema({"flag": {"type": "boolean"}})
        # Exact 0/1 — coerced
        self.assertIs(
            _coerce_tool_args({"flag": 0}, schema)["flag"],
            False,
        )
        self.assertIs(
            _coerce_tool_args({"flag": 1}, schema)["flag"],
            True,
        )
        self.assertIs(
            _coerce_tool_args({"flag": 0.0}, schema)["flag"],
            False,
        )
        self.assertIs(
            _coerce_tool_args({"flag": 1.0}, schema)["flag"],
            True,
        )
        # Other numbers — unchanged
        for v in (2, -1, 2.0, 3.14):
            with self.subTest(value=v):
                self.assertEqual(
                    _coerce_tool_args({"flag": v}, schema)["flag"],
                    v,
                )

    def test_boolean_subschema_is_handled_gracefully(self) -> None:
        """Boolean sub-schemas (true/false) don't crash coercion."""
        root_value = {"x": "42"}
        self.assertEqual(_coerce_tool_args(root_value, True), root_value)
        self.assertEqual(_coerce_tool_args(root_value, False), root_value)

        # x: true — any value is valid, coercion should pass through
        schema = {"type": "object", "properties": {"x": True}}
        result = _coerce_tool_args({"x": "42"}, schema)
        self.assertEqual(result["x"], "42")

        # x: false — value should be preserved, schema validation rejects
        schema_false = {"type": "object", "properties": {"x": False}}
        result = _coerce_tool_args({"x": 42}, schema_false)
        self.assertEqual(result["x"], 42)

        # Boolean schemas resolved through $ref also pass through.
        schema_ref = {
            "type": "object",
            "properties": {
                "allowed": {"$ref": "#/$defs/allow_any"},
                "denied": {"$ref": "#/$defs/deny_all"},
            },
            "$defs": {"allow_any": True, "deny_all": False},
        }
        result = _coerce_tool_args(
            {"allowed": "42", "denied": 42},
            schema_ref,
        )
        self.assertEqual(result, {"allowed": "42", "denied": 42})

    def test_boolean_property_in_object_union_is_handled_gracefully(
        self,
    ) -> None:
        """Boolean property schemas do not break union discrimination."""
        schema = _schema(
            {
                "item": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {"anything": True},
                            "required": ["required_key"],
                        },
                    ],
                },
            },
        )
        value = {"anything": "value"}

        self.assertEqual(
            _coerce_tool_args({"item": value}, schema),
            {"item": value},
        )

    def test_number_rejects_non_finite_strings(self) -> None:
        """NaN, Infinity, -Infinity stay as strings so schema validation
        can reject them with a clear error."""
        schema = _schema({"value": {"type": "number"}})
        for v in ("NaN", "Infinity", "-Infinity", "inf", "-inf"):
            with self.subTest(value=v):
                result = _coerce_tool_args({"value": v}, schema)
                self.assertIsInstance(
                    result["value"],
                    str,
                    f"'{v}' should stay str, not become float",
                )
                self.assertEqual(result["value"], v)


class ToolCoercionIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """Integration tests for the ToolBase and Toolkit invocation paths."""

    async def test_tool_and_middleware_coerced(self) -> None:
        """Base __call__ repairs before tool and middleware execute."""
        calls: list[int | None] = []
        middleware_inputs: list[dict[str, Any]] = []

        class SpyMiddleware(ToolMiddlewareBase):
            """Middleware that records the inputs it receives."""

            async def on_tool_call(
                self,
                tool: Any,
                input_kwargs: Any,
                next_handler: Any,
            ) -> Any:
                middleware_inputs.append(dict(input_kwargs))
                async for chunk in next_handler(**input_kwargs):
                    yield chunk

        async def my_tool(limit: Optional[int] = None) -> str:
            calls.append(limit)
            return f"limit={limit}"

        tool = FunctionTool(my_tool, middlewares=[SpyMiddleware()])
        result = await tool(limit="10")  # type: ignore[arg-type]
        chunks = [chunk async for chunk in result]
        self.assertTrue(chunks)
        self.assertEqual(calls, [10])
        self.assertEqual(middleware_inputs, [{"limit": 10}])

    async def test_toolkit_legacy_call_override(self) -> None:
        """Toolkit repairs for subclasses that override __call__."""
        from agentscope.permission import (  # isort: skip
            PermissionBehavior,
            PermissionDecision,
        )
        from agentscope.state import AgentState  # isort: skip

        received: dict[str, Any] = {}

        class OverrideTool(ToolBase):
            """Tool that overrides __call__ directly with typed params."""

            name = "override_tool"
            description = "Test tool"
            input_schema = _schema({"count": {"type": "integer"}})
            is_concurrency_safe = True
            is_read_only = True

            async def check_permissions(
                self,
                tool_input: Any,
                context: Any,
            ) -> Any:
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="",
                )

            async def __call__(self, count: int) -> ToolChunk:
                received["count"] = count
                return ToolChunk(
                    content=[TextBlock(text=f"count={count}")],
                )

        toolkit = Toolkit(tools=[OverrideTool()])
        tool_call = ToolCallBlock(
            id="call_1",
            name="override_tool",
            input='{"count": "42"}',
        )
        state = AgentState(session_id="s1", agent_id="a1")
        async for _ in toolkit.call_tool(tool_call, state):
            pass
        self.assertEqual(received, {"count": 42})

    async def test_function_tool_dynamic_map(self) -> None:
        """FunctionTool receives repaired values from dict[str, int]."""
        received: list[dict[str, int]] = []

        async def my_tool(counts: dict[str, int]) -> str:
            received.append(counts)
            return "ok"

        await FunctionTool(my_tool)(  # type: ignore[arg-type]
            counts={"a": "1", "b": "2"},
        )
        self.assertEqual(received, [{"a": 1, "b": 2}])
