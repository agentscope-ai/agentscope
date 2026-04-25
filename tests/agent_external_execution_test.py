# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Test the external execution events in the agent class."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, MockModel

from agentscope.agent import Agent
from agentscope.model import ChatResponse
from agentscope.tool import (
    ToolBase,
    Toolkit,
    ToolChunk,
    PermissionDecision,
    PermissionBehavior,
    PermissionContext,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
    ToolResultState,
)
from agentscope.tool._types import RegisteredTool
from agentscope.event import ExternalExecutionResultEvent


class MockExternalSequentialTool(ToolBase):
    """A mock tool that requires external execution (sequential)."""

    name: str = "mock_external_sequential_tool"
    description: str = "A mock external sequential tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_external_tool: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Mock external tool always allows",
            message="Mock external tool always allows",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[TextBlock(text=f"External sequential result: {input}")],
        )


class MockExternalConcurrentTool(ToolBase):
    """A mock tool that requires external execution (concurrent)."""

    name: str = "mock_external_concurrent_tool"
    description: str = "A mock external concurrent tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Mock external tool always allows",
            message="Mock external tool always allows",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[TextBlock(text=f"External concurrent result: {input}")],
        )


class AgentExternalExecutionTest(IsolatedAsyncioTestCase):
    """Test the external execution events in the agent class."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.model = MockModel()
        self.agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.model,
            toolkit=Toolkit(),
        )

    def _get_event_base(self, reply_id: str) -> dict:
        """Get the dict with the basic fields for event assertion."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "reply_id": reply_id,
        }

    def _get_msg_base(self) -> dict:
        """Get the dict with the basic fields for message assertion."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "metadata": {},
            "name": "Friday",
            "role": "assistant",
        }

    async def test_single_external_execution(self) -> None:
        """Test single external execution tool call.

        The agent should:
        1. Generate a tool call that requires external execution
        2. Emit REQUIRE_EXTERNAL_EXECUTION event and pause
        3. Resume when ExternalExecutionResultEvent is provided
        4. Continue execution without calling the model again
        """
        # Register external tool
        ext_tool = MockExternalSequentialTool()
        self.agent.toolkit.tools[ext_tool.name] = RegisteredTool(
            tool=ext_tool,
            group="basic",
        )

        # Create tool call ID
        tool_call_id_1 = "tool_call_1"

        # Set up mock response with tool call (no final text response)
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_external_sequential_tool",
                                input='{"input": "test1"}',
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_external_sequential_tool",
                                input='{"input": "test1"}',
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                [
                    ChatResponse(
                        content=[
                            TextBlock(
                                text="Final response after external execution",
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            TextBlock(
                                text="Final response after external execution",
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
            ],
        )

        # First call: collect events until REQUIRE_EXTERNAL_EXECUTION
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        expected_events = [
            {
                "type": "RUN_STARTED",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_STARTED", "model_name": "mock-model"},
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_external_sequential_tool",
            },
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": '{"input": "test1"}',
            },
            {
                "type": "TOOL_CALL_END",
                "tool_call_id": tool_call_id_1,
            },
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_external_sequential_tool",
            },
            {
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "submitted",
                    },
                ],
            },
        ]

        basic_dict = self._get_event_base(reply_id)
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        # Assert context after first call
        msg_base = self._get_msg_base()
        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "submitted",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Create external execution result event
        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                ToolResultBlock(
                    id=tool_call_id_1,
                    name="mock_external_sequential_tool",
                    output=[
                        TextBlock(text="External sequential result: test1"),
                    ],
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )

        # Second call: resume with external execution result
        events = []
        async for event in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(event.model_dump())

        # Verify events after resumption
        expected_events_resume = [
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": "External sequential result: test1",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_1,
                "state": "success",
            },
            {
                "type": "MODEL_CALL_STARTED",
                "model_name": "mock-model",
            },
            {
                "type": "TEXT_BLOCK_START",
                "block_id": AnyString(),
            },
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "Final response after external execution",
            },
            {
                "type": "TEXT_BLOCK_END",
                "block_id": AnyString(),
            },
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {"type": "RUN_FINISHED", "session_id": session_id},
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_resume],
        )

        # Assert final context
        expected_context_final = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_external_sequential_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "External sequential result: test1",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Final response after external execution",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context_final = [
            {**msg_base, **_} for _ in expected_context_final
        ]
        self.assertListEqual(context_dicts, expected_context_final)

    async def test_sequential_external_execution(self) -> None:
        """Test multiple external execution tool calls in sequential execution.

        The agent should:
        1. Generate multiple tool calls that require external execution
        2. All tools have is_concurrent_safe=False (sequential)
        3. Emit REQUIRE_EXTERNAL_EXECUTION event and pause
        4. Resume when ExternalExecutionResultEvent is provided
        5. Continue execution without calling the model again
        """
        # Register external sequential tool
        ext_tool = MockExternalSequentialTool()
        self.agent.toolkit.tools[ext_tool.name] = RegisteredTool(
            tool=ext_tool,
            group="basic",
        )

        # Create tool call IDs
        tool_call_id_1 = "tool_call_1"
        tool_call_id_2 = "tool_call_2"

        # Set up mock response with multiple tool calls
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_external_sequential_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_2,
                                name="mock_external_sequential_tool",
                                input='{"input": "test2"}',
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_external_sequential_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_2,
                                name="mock_external_sequential_tool",
                                input='{"input": "test2"}',
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                [
                    ChatResponse(
                        content=[
                            TextBlock(
                                text="Final response after external execution",
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            TextBlock(
                                text="Final response after external execution",
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
            ],
        )

        # First call: collect events until REQUIRE_EXTERNAL_EXECUTION
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        expected_events = [
            {
                "type": "RUN_STARTED",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_STARTED", "model_name": "mock-model"},
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_external_sequential_tool",
            },
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": '{"input": "test1"}',
            },
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": tool_call_id_2,
                "tool_call_name": "mock_external_sequential_tool",
            },
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": tool_call_id_2,
                "delta": '{"input": "test2"}',
            },
            {
                "type": "TOOL_CALL_END",
                "tool_call_id": tool_call_id_1,
            },
            {
                "type": "TOOL_CALL_END",
                "tool_call_id": tool_call_id_2,
            },
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_external_sequential_tool",
            },
            {
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "submitted",
                    },
                ],
            },
        ]

        basic_dict = self._get_event_base(reply_id)
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        # Assert context after first call
        msg_base = self._get_msg_base()
        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "submitted",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test2"}',
                        "state": "pending",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Create external execution result event
        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                ToolResultBlock(
                    id=tool_call_id_1,
                    name="mock_external_sequential_tool",
                    output=[
                        TextBlock(text="External sequential result: test1"),
                    ],
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )

        # Second call: resume with external execution result
        events = []
        async for event in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(event.model_dump())

        # Verify events after resumption (sequential execution)
        expected_events_resume = [
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": "External sequential result: test1",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_1,
                "state": "success",
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_2,
                "tool_call_name": "mock_external_sequential_tool",
            },
            {
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test2"}',
                        "state": "submitted",
                    },
                ],
            },
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_resume],
        )

        # Given the external execution result of the second tool call
        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                ToolResultBlock(
                    id=tool_call_id_2,
                    name="mock_external_sequential_tool",
                    output=[
                        TextBlock(text="External sequential result: test2"),
                    ],
                    state=ToolResultState.ERROR,
                ),
            ],
        )

        events = []
        async for evnt in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(evnt.model_dump())

        # Assert the events
        expected_events_after_second_result = [
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_2,
                "delta": "External sequential result: test2",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_2,
                "state": "error",
            },
            {
                "type": "MODEL_CALL_STARTED",
                "model_name": "mock-model",
            },
            {
                "type": "TEXT_BLOCK_START",
                "block_id": AnyString(),
            },
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "Final response after external execution",
            },
            {
                "type": "TEXT_BLOCK_END",
                "block_id": AnyString(),
            },
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {"type": "RUN_FINISHED", "session_id": session_id},
        ]

        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_after_second_result],
        )

        # Assert final context
        expected_context_final = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_external_sequential_tool",
                        "input": '{"input": "test2"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_external_sequential_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "External sequential result: test1",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_external_sequential_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "External sequential result: test2",
                            },
                        ],
                        "state": "error",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Final response after external execution",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context_final = [
            {**msg_base, **_} for _ in expected_context_final
        ]
        self.assertListEqual(context_dicts, expected_context_final)

    async def test_concurrent_external_execution(self) -> None:
        """Test multiple external execution tool calls in concurrent execution.

        The agent should:
        1. Generate multiple tool calls that require external execution
        2. All tools have is_concurrent_safe=True (concurrent)
        3. Emit REQUIRE_EXTERNAL_EXECUTION event and pause
        4. Resume when ExternalExecutionResultEvent is provided
        5. Continue execution without calling the model again
        """
        # Register external concurrent tool
        ext_tool = MockExternalConcurrentTool()
        self.agent.toolkit.tools[ext_tool.name] = RegisteredTool(
            tool=ext_tool,
            group="basic",
        )

        # Create tool call IDs
        tool_call_id_1 = "tool_call_1"
        tool_call_id_2 = "tool_call_2"

        # Set up mock response with multiple tool calls
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_external_concurrent_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_2,
                                name="mock_external_concurrent_tool",
                                input='{"input": "test2"}',
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_external_concurrent_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_2,
                                name="mock_external_concurrent_tool",
                                input='{"input": "test2"}',
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                [
                    ChatResponse(
                        content=[
                            TextBlock(
                                text="Final response after external execution",
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            TextBlock(
                                text="Final response after external execution",
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
            ],
        )

        # First call: collect events until REQUIRE_EXTERNAL_EXECUTION
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        expected_events = [
            {
                "type": "RUN_STARTED",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_STARTED", "model_name": "mock-model"},
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_external_concurrent_tool",
            },
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": '{"input": "test1"}',
            },
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": tool_call_id_2,
                "tool_call_name": "mock_external_concurrent_tool",
            },
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": tool_call_id_2,
                "delta": '{"input": "test2"}',
            },
            {
                "type": "TOOL_CALL_END",
                "tool_call_id": tool_call_id_1,
            },
            {
                "type": "TOOL_CALL_END",
                "tool_call_id": tool_call_id_2,
            },
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_external_concurrent_tool",
            },
            {
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_concurrent_tool",
                        "input": '{"input": "test1"}',
                        "state": "submitted",
                    },
                ],
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_2,
                "tool_call_name": "mock_external_concurrent_tool",
            },
            {
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_external_concurrent_tool",
                        "input": '{"input": "test2"}',
                        "state": "submitted",
                    },
                ],
            },
        ]

        basic_dict = self._get_event_base(reply_id)
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        # Assert context after first call
        msg_base = self._get_msg_base()
        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_concurrent_tool",
                        "input": '{"input": "test1"}',
                        "state": "submitted",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_external_concurrent_tool",
                        "input": '{"input": "test2"}',
                        "state": "submitted",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Create external execution result event
        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                ToolResultBlock(
                    id=tool_call_id_1,
                    name="mock_external_concurrent_tool",
                    output=[
                        TextBlock(text="External concurrent result: test1"),
                    ],
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )

        # Second call: resume with external execution result
        events = []
        async for event in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(event.model_dump())

        expected_concurrent = [
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": "External concurrent result: test1",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_1,
                "state": "success",
            },
            {
                "type": "RUN_FINISHED",
                "session_id": session_id,
            },
        ]

        # Check length matches
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_concurrent],
        )

        # The second tool call result
        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                ToolResultBlock(
                    id=tool_call_id_2,
                    name="mock_external_concurrent_tool",
                    output=[
                        TextBlock(text="External concurrent result: test2"),
                    ],
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(event.model_dump())

        expected_concurrent = [
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_2,
                "delta": "External concurrent result: test2",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_2,
                "state": "success",
            },
            {
                "type": "MODEL_CALL_STARTED",
                "model_name": "mock-model",
            },
            {
                "type": "TEXT_BLOCK_START",
                "block_id": AnyString(),
            },
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "Final response after external execution",
            },
            {
                "type": "TEXT_BLOCK_END",
                "block_id": AnyString(),
            },
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "RUN_FINISHED",
                "session_id": session_id,
            },
        ]

        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_concurrent],
        )

        # Assert final context
        expected_context_final = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_external_concurrent_tool",
                        "input": '{"input": "test1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_external_concurrent_tool",
                        "input": '{"input": "test2"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_external_concurrent_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "External concurrent result: test1",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_external_concurrent_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "External concurrent result: test2",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Final response after external execution",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context_final = [
            {**msg_base, **_} for _ in expected_context_final
        ]
        self.assertListEqual(context_dicts, expected_context_final)

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
