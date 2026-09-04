# -*- coding: utf-8 -*-
"""Unittests for the schema-guided repair of the tool call arguments."""
import unittest
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, MockModel

from agentscope._utils._common import _json_loads_with_repair
from agentscope.agent import Agent, InjectionConfig
from agentscope.exception import ToolJSONDecodeError
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit


class _RecordTool(ToolBase):
    """A tool that records the arguments reaching the permission checking
    and the execution. It overrides ``__call__`` on purpose, so that the
    arguments are only repaired by ``Toolkit.call_tool``."""

    name = "record"
    description = "Record the given value."
    is_concurrency_safe = True
    is_read_only = False

    def __init__(self, value_schema: dict[str, Any]) -> None:
        """Initialize the tool with the schema of the ``value`` argument."""
        super().__init__()
        self.input_schema = {
            "type": "object",
            "properties": {"value": value_schema},
            "required": ["value"],
        }
        self.permission_inputs: list[dict[str, Any]] = []
        self.executed: list[Any] = []

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Record and allow the tool input."""
        self.permission_inputs.append(tool_input)
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Allowed",
        )

    async def __call__(self, value: Any) -> ToolChunk:
        """Record the executed value."""
        self.executed.append(value)
        return ToolChunk(content=[TextBlock(text="Recorded")])


class JsonLoadsWithRepairTest(unittest.TestCase):
    """Unittest for the `_json_loads_with_repair` function."""

    def test_repair_argument_types(self) -> None:
        """Test repairing the argument types with the given schema."""
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "verbose": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        }
        self.assertDictEqual(
            _json_loads_with_repair(
                '{"count": "42", "verbose": "true", "tags": "a"}',
                schema,
            ),
            {"count": 42, "verbose": True, "tags": ["a"]},
        )
        # A malformed JSON string is repaired in both syntax and types
        self.assertDictEqual(
            _json_loads_with_repair("{count: '42', verbose: 'true',", schema),
            {"count": 42, "verbose": True},
        )
        # Without a schema, the arguments are kept as they are
        self.assertDictEqual(
            _json_loads_with_repair('{"count": "42"}'),
            {"count": "42"},
        )

    def test_keep_unrepairable_arguments(self) -> None:
        """Test that unrepairable arguments are left to the validation."""
        self.assertDictEqual(
            _json_loads_with_repair(
                '{"count": "many"}',
                {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                },
            ),
            {"count": "many"},
        )
        # Dropping an argument is a rewrite rather than a type repair
        self.assertDictEqual(
            _json_loads_with_repair(
                '{"count": "42", "verbse": true}',
                {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "additionalProperties": False,
                },
            ),
            {"count": "42", "verbse": True},
        )

    def test_reject_invalid_arguments(self) -> None:
        """Test the arguments that cannot be loaded into a valid dict."""
        for json_str in ['"hello"', "[1, 2]"]:
            with self.subTest(json_str=json_str):
                with self.assertRaises(ToolJSONDecodeError):
                    _json_loads_with_repair(json_str)

        # NaN and Infinity pass jsonschema as numbers, but silently bypass
        # the minimum/maximum constraints
        for json_str in ['{"ratio": NaN}', '{"ratio": "1e400"}']:
            with self.subTest(json_str=json_str):
                with self.assertRaises(ToolJSONDecodeError):
                    _json_loads_with_repair(
                        json_str,
                        {
                            "type": "object",
                            "properties": {"ratio": {"type": "number"}},
                        },
                    )


class ToolCallArgumentRepairTest(IsolatedAsyncioTestCase):
    """Unittest for repairing the arguments within an agent reply."""

    async def test_repaired_arguments_reach_permission_and_execution(
        self,
    ) -> None:
        """Test that the repaired arguments are used consistently, while the
        tool call input keeps what the model generated."""
        tool = _RecordTool({"type": "integer"})
        tool_call = ToolCallBlock(
            id="record_call_0",
            name="record",
            input='{"value": "42"}',
        )
        model = MockModel()
        model.set_responses(
            [
                [ChatResponse(content=[tool_call], is_last=True)],
                [
                    ChatResponse(
                        content=[TextBlock(text="Done")],
                        is_last=True,
                    ),
                ],
            ],
        )
        agent = Agent(
            name="Friday",
            system_prompt="You're a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[tool]),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

        await agent.reply(UserMsg(name="user", content="Record 42"))

        self.assertListEqual(tool.permission_inputs, [{"value": 42}])
        self.assertListEqual(tool.executed, [42])
        self.assertEqual(tool_call.input, '{"value": "42"}')
        self.assertListEqual(
            [
                block.model_dump()
                for block in agent.state.context[-1].get_content_blocks(
                    "tool_result",
                )
            ],
            [
                {
                    "type": "tool_result",
                    "id": "record_call_0",
                    "name": "record",
                    "output": [
                        {
                            "type": "text",
                            "text": "Recorded",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "state": "success",
                    "metadata": {},
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
        )

    async def test_unrepairable_arguments_are_rejected(self) -> None:
        """Test that unrepairable arguments are reported by the validation,
        without reaching the permission checking or the execution."""
        tool = _RecordTool({"type": "integer"})
        tool_call = ToolCallBlock(
            id="record_call_0",
            name="record",
            input='{"value": "many"}',
        )
        model = MockModel()
        model.set_responses(
            [
                [ChatResponse(content=[tool_call], is_last=True)],
                [
                    ChatResponse(
                        content=[TextBlock(text="Done")],
                        is_last=True,
                    ),
                ],
            ],
        )
        agent = Agent(
            name="Friday",
            system_prompt="You're a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[tool]),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

        await agent.reply(UserMsg(name="user", content="Record many"))

        self.assertListEqual(tool.permission_inputs, [])
        self.assertListEqual(tool.executed, [])
        self.assertEqual(tool_call.input, '{"value": "many"}')
        self.assertListEqual(
            [
                block.model_dump()
                for block in agent.state.context[-1].get_content_blocks(
                    "tool_result",
                )
            ],
            [
                {
                    "type": "tool_result",
                    "id": "record_call_0",
                    "name": "record",
                    "output": "Input validation failed for tool 'record': "
                    "'many' is not of type 'integer'",
                    "state": "error",
                    "metadata": {},
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
