# -*- coding: utf-8 -*-
"""Agent integration tests for schema-guided tool argument repair."""
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.model import ChatResponse
from agentscope.tool import ToolBase, Toolkit, ToolChunk
from agentscope.permission import (
    PermissionDecision,
    PermissionBehavior,
    PermissionContext,
)
from agentscope.message import TextBlock, ToolCallBlock, UserMsg


class _RecordingTool(ToolBase):
    """Record permission and execution inputs."""

    name = "record"
    description = "Record a value"
    is_concurrency_safe = True
    is_read_only = False

    def __init__(self, value_schema: dict[str, Any]) -> None:
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
        self.permission_inputs.append(dict(tool_input))
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Always allow",
        )

    async def __call__(self, value: Any) -> ToolChunk:
        self.executed.append(value)
        return ToolChunk(content=[TextBlock(text="Executed")])


class AgentToolSchemaRepairTest(IsolatedAsyncioTestCase):
    """Argument repair tests through the Agent loop."""

    async def _run_tool(
        self,
        tool: _RecordingTool,
        input_json: str,
    ) -> tuple[ToolCallBlock, list[str]]:
        """Run one tool call and collect its result states."""
        tool_call = ToolCallBlock(
            id="call",
            name=tool.name,
            input=input_json,
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
            system_prompt="You are a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[tool]),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        states = []
        async for event in agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            data = event.model_dump(mode="json")
            if data["type"] == "TOOL_RESULT_END":
                states.append(data["state"])
        return tool_call, states

    async def test_repaired_input_is_used_consistently(self) -> None:
        """Persist repaired input for permission checks and execution."""
        tool = _RecordingTool({"type": "integer"})
        tool_call, states = await self._run_tool(tool, '{"value": "42"}')
        self.assertEqual(json.loads(tool_call.input), {"value": 42})
        self.assertEqual(tool.permission_inputs, [{"value": 42}])
        self.assertEqual(tool.executed, [42])
        self.assertIsInstance(tool.executed[0], int)
        self.assertEqual(states, ["success"])

    async def test_invalid_input_never_reaches_permissions_or_execution(
        self,
    ) -> None:
        """Reject invalid values without overwriting the original input."""
        cases: tuple[tuple[dict[str, Any], str], ...] = (
            ({"type": "integer", "minimum": 100}, '{"value": "42"}'),
            ({"type": "integer"}, '{"value": "invalid"}'),
            (
                {"type": "number", "minimum": 0, "maximum": 1},
                '{"value": NaN}',
            ),
            ({"type": "string"}, '{"value": NaN}'),
        )
        for schema, original in cases:
            with self.subTest(schema=schema, original=original):
                tool = _RecordingTool(schema)
                tool_call, states = await self._run_tool(tool, original)
                self.assertEqual(tool.permission_inputs, [])
                self.assertEqual(tool.executed, [])
                self.assertEqual(tool_call.input, original)
                self.assertEqual(states, ["error"])
