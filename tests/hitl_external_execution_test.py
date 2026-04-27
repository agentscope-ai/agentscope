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
        self.tool_call_id_1 = "tool_call_1"
        self.tool_call_id_2 = "tool_call_2"
        self.user_input_text = "Test"
        self.tool_input_1 = '{"input": "test1"}'
        self.tool_input_2 = '{"input": "test2"}'
        self.sequential_tool_name = "mock_external_sequential_tool"
        self.concurrent_tool_name = "mock_external_concurrent_tool"
        self.sequential_result_1 = "External sequential result: test1"
        self.sequential_result_2 = "External sequential result: test2"
        self.concurrent_result_1 = "External concurrent result: test1"
        self.concurrent_result_2 = "External concurrent result: test2"
        self.final_response_text = "Final response after external execution"
        self.final_text_events = [
            {
                "type": "MODEL_CALL_START",
                "model_name": "mock-model",
            },
            {
                "type": "TEXT_BLOCK_START",
                "block_id": AnyString(),
            },
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": self.final_response_text,
            },
            {
                "type": "TEXT_BLOCK_END",
                "block_id": AnyString(),
            },
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
        ]
        self.final_mock_responses = [
            ChatResponse(
                content=[
                    TextBlock(text=self.final_response_text),
                ],
                is_last=False,
                usage=None,
            ),
            ChatResponse(
                content=[
                    TextBlock(text=self.final_response_text),
                ],
                is_last=True,
                usage=None,
            ),
        ]

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

    def _get_tool_call_events(
        self,
        id: str,
        name: str,
        delta: str,
    ) -> list[dict]:
        """Helper method to get the expected tool call events."""
        return [
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": id,
                "tool_call_name": name,
            },
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": id,
                "delta": delta,
            },
            {
                "type": "TOOL_CALL_END",
                "tool_call_id": id,
            },
        ]

    def _get_tool_result_events(
        self,
        id: str,
        result: str,
        state: str = "success",
    ) -> list[dict]:
        """Helper method to get the expected tool result events."""
        return [
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": id,
                "delta": result,
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": id,
                "state": state,
            },
        ]

    def _get_require_external_execution_events(
        self,
        reply_id: str,
        id: str,
        name: str,
        tool_input: str,
    ) -> list[dict]:
        """Helper method to get the expected external execution events."""
        return [
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": id,
                "tool_call_name": name,
            },
            {
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": id,
                        "name": name,
                        "input": tool_input,
                        "state": "submitted",
                    },
                ],
            },
        ]

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

        # Set up mock response with tool call (no final text response)
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=self.sequential_tool_name,
                                input=self.tool_input_1,
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=self.sequential_tool_name,
                                input=self.tool_input_1,
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        # First call: collect events until REQUIRE_EXTERNAL_EXECUTION
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content=self.user_input_text),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        expected_events = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            *self._get_tool_call_events(
                self.tool_call_id_1,
                self.sequential_tool_name,
                self.tool_input_1,
            ),
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            *self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_1,
                self.sequential_tool_name,
                self.tool_input_1,
            ),
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
                "content": self.user_input_text,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
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
                    id=self.tool_call_id_1,
                    name=self.sequential_tool_name,
                    output=[
                        TextBlock(text=self.sequential_result_1),
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
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.sequential_result_1,
            ),
            *self.final_text_events,
            {"type": "REPLY_END", "session_id": session_id},
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
                "content": self.user_input_text,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": self.sequential_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": self.sequential_result_1,
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": self.final_response_text,
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

        # Set up mock response with multiple tool calls
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=self.sequential_tool_name,
                                input=self.tool_input_1,
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name=self.sequential_tool_name,
                                input=self.tool_input_2,
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=self.sequential_tool_name,
                                input=self.tool_input_1,
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name=self.sequential_tool_name,
                                input=self.tool_input_2,
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        # First call: collect events until REQUIRE_EXTERNAL_EXECUTION
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content=self.user_input_text),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id
        tool_call_1_events = self._get_tool_call_events(
            self.tool_call_id_1,
            self.sequential_tool_name,
            self.tool_input_1,
        )
        tool_call_2_events = self._get_tool_call_events(
            self.tool_call_id_2,
            self.sequential_tool_name,
            self.tool_input_2,
        )

        expected_events = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            *tool_call_1_events[:2],
            *tool_call_2_events[:2],
            tool_call_1_events[2],
            tool_call_2_events[2],
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            *self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_1,
                self.sequential_tool_name,
                self.tool_input_1,
            ),
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
                "content": self.user_input_text,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "submitted",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_2,
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
                    id=self.tool_call_id_1,
                    name=self.sequential_tool_name,
                    output=[
                        TextBlock(text=self.sequential_result_1),
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
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.sequential_result_1,
            ),
            *self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_2,
                self.sequential_tool_name,
                self.tool_input_2,
            ),
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
                    id=self.tool_call_id_2,
                    name=self.sequential_tool_name,
                    output=[
                        TextBlock(text=self.sequential_result_2),
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
            *self._get_tool_result_events(
                self.tool_call_id_2,
                self.sequential_result_2,
                state="error",
            ),
            *self.final_text_events,
            {"type": "REPLY_END", "session_id": session_id},
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
                "content": self.user_input_text,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_2,
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": self.sequential_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": self.sequential_result_1,
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": self.sequential_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": self.sequential_result_2,
                            },
                        ],
                        "state": "error",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": self.final_response_text,
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

        # Set up mock response with multiple tool calls
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=self.concurrent_tool_name,
                                input=self.tool_input_1,
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name=self.concurrent_tool_name,
                                input=self.tool_input_2,
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=self.concurrent_tool_name,
                                input=self.tool_input_1,
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name=self.concurrent_tool_name,
                                input=self.tool_input_2,
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        # First call: collect events until REQUIRE_EXTERNAL_EXECUTION
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content=self.user_input_text),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id
        tool_call_1_events = self._get_tool_call_events(
            self.tool_call_id_1,
            self.concurrent_tool_name,
            self.tool_input_1,
        )
        tool_call_2_events = self._get_tool_call_events(
            self.tool_call_id_2,
            self.concurrent_tool_name,
            self.tool_input_2,
        )

        expected_events = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            *tool_call_1_events[:2],
            *tool_call_2_events[:2],
            tool_call_1_events[2],
            tool_call_2_events[2],
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            *self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_1,
                self.concurrent_tool_name,
                self.tool_input_1,
            ),
            *self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_2,
                self.concurrent_tool_name,
                self.tool_input_2,
            ),
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
                "content": self.user_input_text,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_1,
                        "state": "submitted",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_2,
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
                    id=self.tool_call_id_1,
                    name=self.concurrent_tool_name,
                    output=[
                        TextBlock(text=self.concurrent_result_1),
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
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.concurrent_result_1,
            ),
        ]

        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_concurrent],
        )

        # The second tool call result
        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                ToolResultBlock(
                    id=self.tool_call_id_2,
                    name=self.concurrent_tool_name,
                    output=[
                        TextBlock(text=self.concurrent_result_2),
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
            *self._get_tool_result_events(
                self.tool_call_id_2,
                self.concurrent_result_2,
            ),
            *self.final_text_events,
            {
                "type": "REPLY_END",
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
                "content": self.user_input_text,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_1,
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_2,
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": self.concurrent_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": self.concurrent_result_1,
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": self.concurrent_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": self.concurrent_result_2,
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": self.final_response_text,
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context_final = [
            {**msg_base, **_} for _ in expected_context_final
        ]
        self.assertListEqual(context_dicts, expected_context_final)

    async def test_concurrent_external_execution_in_single_event(self) -> None:
        """Test concurrent external execution when two results arrive together.

        The agent should:
        1. Generate multiple external tool calls in concurrent mode
        2. Emit one require event per tool call during the initial run
        3. Resume when a single ExternalExecutionResultEvent carries both
           results
        4. Continue reasoning only after both results are applied
        """
        ext_tool = MockExternalConcurrentTool()
        self.agent.toolkit.tools[ext_tool.name] = RegisteredTool(
            tool=ext_tool,
            group="basic",
        )

        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=self.concurrent_tool_name,
                                input=self.tool_input_1,
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name=self.concurrent_tool_name,
                                input=self.tool_input_2,
                            ),
                        ],
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=self.concurrent_tool_name,
                                input=self.tool_input_1,
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name=self.concurrent_tool_name,
                                input=self.tool_input_2,
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content=self.user_input_text),
        ):
            events.append(event.model_dump())

        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id
        basic_dict = self._get_event_base(reply_id)
        msg_base = self._get_msg_base()
        tool_call_1_events = self._get_tool_call_events(
            self.tool_call_id_1,
            self.concurrent_tool_name,
            self.tool_input_1,
        )
        tool_call_2_events = self._get_tool_call_events(
            self.tool_call_id_2,
            self.concurrent_tool_name,
            self.tool_input_2,
        )

        expected_events = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            *tool_call_1_events[:2],
            *tool_call_2_events[:2],
            tool_call_1_events[2],
            tool_call_2_events[2],
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            *self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_1,
                self.concurrent_tool_name,
                self.tool_input_1,
            ),
            *self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_2,
                self.concurrent_tool_name,
                self.tool_input_2,
            ),
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": self.user_input_text,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_1,
                        "state": "submitted",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_2,
                        "state": "submitted",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                ToolResultBlock(
                    id=self.tool_call_id_1,
                    name=self.concurrent_tool_name,
                    output=[
                        TextBlock(text=self.concurrent_result_1),
                    ],
                    state=ToolResultState.SUCCESS,
                ),
                ToolResultBlock(
                    id=self.tool_call_id_2,
                    name=self.concurrent_tool_name,
                    output=[
                        TextBlock(text=self.concurrent_result_2),
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

        expected_events_resume = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.concurrent_result_1,
            ),
            *self._get_tool_result_events(
                self.tool_call_id_2,
                self.concurrent_result_2,
            ),
            *self.final_text_events,
            {
                "type": "REPLY_END",
                "session_id": session_id,
            },
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_resume],
        )

        expected_context_final = [
            {
                "name": "user",
                "role": "user",
                "content": self.user_input_text,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_1,
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_2,
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": self.concurrent_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": self.concurrent_result_1,
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": self.concurrent_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": self.concurrent_result_2,
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": self.final_response_text,
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
