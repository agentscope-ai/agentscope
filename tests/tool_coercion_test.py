# -*- coding: utf-8 -*-
"""Regression tests for schema-guided tool argument repair."""

import json
import unittest
from typing import Any
from unittest.mock import patch

from agentscope._utils._common import _json_loads_with_repair
from agentscope.exception import ToolJSONDecodeError
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.state import AgentState
from agentscope.tool import (
    FunctionTool,
    ToolBase,
    ToolChunk,
    Toolkit,
    ToolMiddlewareBase,
)


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    """Build an object schema."""
    return {"type": "object", "properties": properties}


def _repair(
    kwargs: dict[str, Any],
    schema: dict[str, Any] | bool,
) -> dict[str, Any]:
    """Repair serialized arguments through the shared helper."""
    return _json_loads_with_repair(json.dumps(kwargs), schema)


class ToolSchemaRepairTest(unittest.TestCase):
    """Parsing and dependency regression tests."""

    def test_valid_json_still_uses_schema(self) -> None:
        """Repair valid JSON, keeping the no-schema fast path unchanged."""
        schema = _schema({"count": {"type": "integer"}})
        repaired = _json_loads_with_repair('{"count": "42"}', schema)
        self.assertEqual(repaired, {"count": 42})
        self.assertEqual(_repair(repaired, schema), repaired)
        with patch("json_repair.repair_json") as repair:
            self.assertEqual(
                _json_loads_with_repair('{"count": "42"}'),
                {"count": "42"},
            )
            repair.assert_not_called()

    def test_malformed_json_is_repaired(self) -> None:
        """Syntax repair continues to work with and without a schema."""
        self.assertEqual(
            _json_loads_with_repair("{count: '42',}"),
            {"count": "42"},
        )
        self.assertEqual(
            _json_loads_with_repair(
                "{count: '42',}",
                _schema({"count": {"type": "integer"}}),
            ),
            {"count": 42},
        )

    def test_dependency_repairs_encoded_containers_and_boolean(self) -> None:
        """Cover the minimum dependency version's required repairs."""
        schema = _schema(
            {
                "flag": {"type": "boolean"},
                "items": {"type": "array", "items": {"type": "integer"}},
                "config": {
                    "type": "object",
                    "properties": {"port": {"type": "integer"}},
                },
            },
        )
        self.assertEqual(
            _repair(
                {
                    "flag": 1,
                    "items": '["1", "2"]',
                    "config": '{"port": "8080"}',
                },
                schema,
            ),
            {"flag": True, "items": [1, 2], "config": {"port": 8080}},
        )

    def test_composite_keeps_root_reference_context(self) -> None:
        """Keep root references visible inside a composite schema."""
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
        self.assertEqual(
            _repair({"value": {"x": "42"}}, schema),
            {"value": {"x": 42}},
        )

    def test_boolean_schemas_are_not_treated_as_missing(self) -> None:
        """An explicit False schema must not use the no-schema fast path."""
        value = {"x": "42"}
        self.assertEqual(_repair(value, True), value)
        self.assertEqual(_repair(value, {}), value)
        with self.assertRaises(ToolJSONDecodeError):
            _repair(value, False)

    def test_lossy_integer_repairs_are_rejected(self) -> None:
        """Reject precision loss in scalar and nested arguments."""
        schema = _schema({"value": {"type": "integer"}})
        self.assertEqual(
            _repair({"value": "9007199254740993"}, schema),
            {"value": 9007199254740993},
        )
        for value in ("1e23", "100000000000000000000000.0"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ToolJSONDecodeError, "precision"):
                    _repair({"value": value}, schema)
        with self.assertRaisesRegex(ToolJSONDecodeError, "precision"):
            _json_loads_with_repair("{value: '1e23',}", schema)
        nested = _schema(
            {"values": {"type": "array", "items": {"type": "integer"}}},
        )
        for values in (["1e23"], "1e23", '["1e23"]'):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ToolJSONDecodeError, "precision"):
                    _repair({"values": values}, nested)

    def test_non_finite_numbers_are_rejected(self) -> None:
        """Reject non-finite values before and after schema repair."""
        for value in (float("nan"), float("inf"), float("-inf")):
            for expected_type in ("number", "string"):
                with self.subTest(value=value, expected_type=expected_type):
                    with self.assertRaises(ToolJSONDecodeError):
                        _repair(
                            {"value": value},
                            _schema({"value": {"type": expected_type}}),
                        )
        for value in ("NaN", "1e999"):
            with self.subTest(value=value):
                with self.assertRaises(ToolJSONDecodeError):
                    _repair(
                        {"value": value},
                        _schema({"value": {"type": "number"}}),
                    )
        self.assertEqual(
            _repair({"value": "NaN"}, _schema({"value": {"type": "string"}})),
            {"value": "NaN"},
        )

    def test_errors_use_the_shared_exception(self) -> None:
        """Reject non-object inputs and retain useful schema error details."""
        for value in ('"hello"', "[]", "null"):
            with self.subTest(value=value):
                with self.assertRaises(ToolJSONDecodeError):
                    _json_loads_with_repair(value)
        with self.assertRaisesRegex(ToolJSONDecodeError, "TRUNCATE"):
            _json_loads_with_repair(
                json.dumps({"count": "x" * 300}),
                _schema({"count": {"type": "integer"}}),
            )


class ToolSchemaRepairIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """ToolBase and Toolkit integration tests."""

    async def test_toolkit_legacy_call_override(self) -> None:
        """Toolkit repairs for subclasses that bypass ToolBase.__call__."""
        received: list[int] = []

        class OverrideTool(FunctionTool):
            """Record kwargs without passing through base-class repair."""

            async def __call__(self, count: int) -> ToolChunk:
                received.append(count)
                return ToolChunk(content=[TextBlock(text="Executed")])

        def integer_tool(count: int) -> str:
            return str(count)

        tool = OverrideTool(integer_tool)
        responses = [
            chunk
            async for chunk in Toolkit(tools=[tool]).call_tool(
                ToolCallBlock(
                    id="call",
                    name=tool.name,
                    input='{"count": "42"}',
                ),
                AgentState(),
            )
        ]
        self.assertEqual(received, [42])
        self.assertEqual(responses[-1].state, ToolResultState.SUCCESS)

    async def test_state_and_repaired_arguments_reach_middleware(self) -> None:
        """Repair before middleware while preserving injected state."""
        calls: list[dict[str, Any]] = []
        middleware_inputs: list[dict[str, Any]] = []

        class SpyMiddleware(ToolMiddlewareBase):
            """Record both repaired parameters and injected state."""

            async def on_tool_call(
                self,
                tool: ToolBase,
                input_kwargs: Any,
                next_handler: Any,
            ) -> Any:
                middleware_inputs.append(dict(input_kwargs))
                async for chunk in next_handler(**input_kwargs):
                    yield chunk

        async def stateful(count: int, **kwargs: Any) -> str:
            calls.append({"count": count, **kwargs})
            return "ok"

        tool = FunctionTool(
            stateful,
            is_state_injected=True,
            middlewares=[SpyMiddleware()],
        )
        tool.input_schema["additionalProperties"] = False
        state = AgentState()
        result = await tool(count="42", _agent_state=state, extra="drop")
        async for _ in result:
            pass
        responses = [
            chunk
            async for chunk in Toolkit(tools=[tool]).call_tool(
                ToolCallBlock(
                    id="call",
                    name=tool.name,
                    input='{"count": "43", "extra": "drop"}',
                ),
                state,
            )
        ]
        for recorded in (calls, middleware_inputs):
            self.assertEqual(
                recorded,
                [
                    {"count": 42, "_agent_state": state},
                    {"count": 43, "_agent_state": state},
                ],
            )
            self.assertTrue(
                all(item["_agent_state"] is state for item in recorded),
            )
        self.assertEqual(responses[-1].state, ToolResultState.SUCCESS)

    async def test_direct_python_and_schemaless_calls_are_preserved(
        self,
    ) -> None:
        """Preserve native objects and do not require a schema."""
        received: list[Any] = []

        async def passthrough(value: Any) -> str:
            received.append(value)
            return "ok"

        tool = FunctionTool(passthrough)
        payload = object()
        await tool(value=payload)
        self.assertIs(received[0], payload)
        del tool.input_schema
        await tool(value="42")
        self.assertEqual(received[1], "42")

    async def test_unsafe_arguments_do_not_execute(self) -> None:
        """Best-effort repair must not bypass numeric safety checks."""
        received: list[int] = []

        async def integer_tool(count: int) -> str:
            received.append(count)
            return "ok"

        tool = FunctionTool(integer_tool)
        toolkit = Toolkit(tools=[tool])
        for expected_type, value in (
            ("integer", "1e23"),
            ("number", "1e999"),
            ("number", float("nan")),
            ("number", float("inf")),
        ):
            tool.input_schema = _schema({"count": {"type": expected_type}})
            kwargs = {"count": value}
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ToolJSONDecodeError):
                    await tool(**kwargs)
                tool_call = ToolCallBlock(
                    id="call",
                    name=tool.name,
                    input=json.dumps(kwargs),
                )
                responses = [
                    chunk
                    async for chunk in toolkit.call_tool(
                        tool_call,
                        AgentState(),
                    )
                ]
                self.assertEqual(responses[-1].state, ToolResultState.ERROR)
                self.assertEqual(tool_call.input, json.dumps(kwargs))
        self.assertEqual(received, [])
