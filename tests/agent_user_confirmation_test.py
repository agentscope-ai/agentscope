# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Test the user confirmation events in the agent class."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString, MockModel

from agentscope.event._event import ConfirmResult
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
    UserMsg,
)
from agentscope.tool._types import RegisteredTool
from agentscope.event import UserConfirmResultEvent


class MockUserConfirmSequentialTool(ToolBase):
    """A mock tool that requires user confirmation (sequential)."""

    name: str = "mock_user_confirm_sequential_tool"
    description: str = "A mock user confirm sequential tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="Mock tool requires user confirmation",
            message="Mock tool requires user confirmation",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[
                TextBlock(text=f"User confirm sequential result: {input}"),
            ],
        )


class MockUserConfirmConcurrentTool(ToolBase):
    """A mock tool that requires user confirmation (concurrent)."""

    name: str = "mock_user_confirm_concurrent_tool"
    description: str = "A mock user confirm concurrent tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="Mock tool requires user confirmation",
            message="Mock tool requires user confirmation",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[
                TextBlock(text=f"User confirm concurrent result: {input}"),
            ],
        )


class AgentUserConfirmationTest(IsolatedAsyncioTestCase):
    """Test the user confirmation events in the agent class."""

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
        name: str,
        result: str,
    ) -> list[dict]:
        """Helper method to get the expected tool result events."""
        return [
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": id,
                "tool_call_name": name,
            },
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": id,
                "delta": result,
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": id,
                "state": "success",
            },
        ]

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
        self.final_text_events = [
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
                "delta": "Result 1",
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
        ]

        self.final_mock_responses = [
            ChatResponse(
                content=[
                    TextBlock(text="Result 1"),
                ],
                is_last=False,
            ),
            ChatResponse(
                content=[
                    TextBlock(text="Result 1"),
                ],
                is_last=True,
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

    async def test_single_user_confirmation(self) -> None:
        """Test single user confirmation tool call.

        The agent should:
        1. Generate a tool call that requires user confirmation
        2. Emit REQUIRE_USER_CONFIRM event and pause
        3. Resume when UserConfirmResultEvent is provided
        4. Execute the tool and continue
        """
        # Register user confirm tool
        confirm_tool = MockUserConfirmSequentialTool()
        self.agent.toolkit.tools[confirm_tool.name] = RegisteredTool(
            tool=confirm_tool,
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
                                name="mock_user_confirm_sequential_tool",
                                input='{"input": "test1"}',
                            ),
                        ],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_user_confirm_sequential_tool",
                                input='{"input": "test1"}',
                            ),
                        ],
                        is_last=True,
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        # First call: collect events until REQUIRE_USER_CONFIRM
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
            *self._get_tool_call_events(
                self.tool_call_id_1,
                "mock_user_confirm_sequential_tool",
                '{"input": "test1"}',
            ),
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "asking",
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
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "asking",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Create user confirmation result event
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=tool_call_id_1,
                        name="mock_user_confirm_sequential_tool",
                        input='{"input": "test1"}',
                    ),
                ),
            ],
        )

        # Second call: resume with user confirmation result
        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        # Verify events after resumption
        expected_events_resume = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                "mock_user_confirm_sequential_tool",
                "User confirm sequential result: test1",
            ),
            *self.final_text_events,
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
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_user_confirm_sequential_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "User confirm sequential result: "
                                "test1",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Result 1",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context_final = [
            {**msg_base, **_} for _ in expected_context_final
        ]
        self.assertListEqual(context_dicts, expected_context_final)

    async def test_sequential_user_confirmation(self) -> None:
        """Test multiple user confirmation tool calls in sequential execution.

        The agent should:
        1. Generate multiple tool calls that require user confirmation
        2. All tools have is_concurrent_safe=False (sequential)
        3. Emit REQUIRE_USER_CONFIRM event and pause
        4. Resume when UserConfirmResultEvent is provided
        5. Execute the tools and continue
        """
        # Register user confirm sequential tool
        confirm_tool = MockUserConfirmSequentialTool()
        self.agent.toolkit.tools[confirm_tool.name] = RegisteredTool(
            tool=confirm_tool,
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
                                name="mock_user_confirm_sequential_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_2,
                                name="mock_user_confirm_sequential_tool",
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
                                name="mock_user_confirm_sequential_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_2,
                                name="mock_user_confirm_sequential_tool",
                                input='{"input": "test2"}',
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        # First call: collect events until REQUIRE_USER_CONFIRM
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        tool_call_1_events = self._get_tool_call_events(
            self.tool_call_id_1,
            "mock_user_confirm_sequential_tool",
            '{"input": "test1"}',
        )
        tool_call_2_events = self._get_tool_call_events(
            self.tool_call_id_2,
            "mock_user_confirm_sequential_tool",
            '{"input": "test2"}',
        )

        expected_events = [
            {
                "type": "RUN_STARTED",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_STARTED", "model_name": "mock-model"},
            *tool_call_1_events[:2],
            *tool_call_2_events[:2],
            tool_call_1_events[2],
            tool_call_2_events[2],
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "asking",
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
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "asking",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test2"}',
                        "state": "pending",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Create user confirmation result event
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=tool_call_id_1,
                        name="mock_user_confirm_sequential_tool",
                        input='{"input": "test1"}',
                    ),
                ),
            ],
        )

        # resume with user confirmation result
        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        # Verify events after resumption (sequential execution)
        expected_events_resume = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                "mock_user_confirm_sequential_tool",
                "User confirm sequential result: test1",
            ),
            {
                "type": "REQUIRE_USER_CONFIRM",
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": "tool_call_2",
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test2"}',
                        "state": "asking",
                    },
                ],
            },
        ]

        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_resume],
        )

        # Confirm the second tool call
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=tool_call_id_2,
                        name="mock_user_confirm_sequential_tool",
                        input='{"input": "test2"}',
                    ),
                ),
            ],
        )

        # Second call: resume with user confirmation result
        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        expected_events_resume_2 = [
            *self._get_tool_result_events(
                self.tool_call_id_2,
                "mock_user_confirm_sequential_tool",
                "User confirm sequential result: test2",
            ),
            *self.final_text_events,
            {"type": "RUN_FINISHED", "session_id": session_id},
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_resume_2],
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
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_user_confirm_sequential_tool",
                        "input": '{"input": "test2"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_user_confirm_sequential_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "User confirm sequential result: "
                                "test1",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_user_confirm_sequential_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "User confirm sequential result: "
                                "test2",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Result 1",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context_final = [
            {**msg_base, **_} for _ in expected_context_final
        ]
        self.assertListEqual(context_dicts, expected_context_final)

    async def test_concurrent_user_confirmation(self) -> None:
        """Test multiple user confirmation tool calls in concurrent execution.

        The agent should:
        1. Generate multiple tool calls that require user confirmation
        2. All tools have is_concurrent_safe=True (concurrent)
        3. Emit REQUIRE_USER_CONFIRM event and pause
        4. Resume when UserConfirmResultEvent is provided
        5. Execute the tools and continue
        """
        # Register user confirm concurrent tool
        confirm_tool = MockUserConfirmConcurrentTool()
        self.agent.toolkit.tools[confirm_tool.name] = RegisteredTool(
            tool=confirm_tool,
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
                                name="mock_user_confirm_concurrent_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name="mock_user_confirm_concurrent_tool",
                                input='{"input": "test2"}',
                            ),
                        ],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name="mock_user_confirm_concurrent_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name="mock_user_confirm_concurrent_tool",
                                input='{"input": "test2"}',
                            ),
                        ],
                        is_last=True,
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        # First call: collect events until REQUIRE_USER_CONFIRM
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        tool_call_1_events = self._get_tool_call_events(
            self.tool_call_id_1,
            "mock_user_confirm_concurrent_tool",
            '{"input": "test1"}',
        )
        tool_call_2_events = self._get_tool_call_events(
            self.tool_call_id_2,
            "mock_user_confirm_concurrent_tool",
            '{"input": "test2"}',
        )

        expected_events = [
            {
                "type": "RUN_STARTED",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_STARTED", "model_name": "mock-model"},
            *tool_call_1_events[:2],
            *tool_call_2_events[:2],
            tool_call_1_events[2],
            tool_call_2_events[2],
            {
                "type": "MODEL_CALL_ENDED",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_1,
                        "name": "mock_user_confirm_concurrent_tool",
                        "input": '{"input": "test1"}',
                        "state": "asking",
                    },
                ],
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": "mock_user_confirm_concurrent_tool",
                        "input": '{"input": "test2"}',
                        "state": "asking",
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
                        "id": self.tool_call_id_1,
                        "name": "mock_user_confirm_concurrent_tool",
                        "input": '{"input": "test1"}',
                        "state": "asking",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": "mock_user_confirm_concurrent_tool",
                        "input": '{"input": "test2"}',
                        "state": "asking",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Create user confirmation result event
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=self.tool_call_id_1,
                        name="mock_user_confirm_concurrent_tool",
                        input='{"input": "test1"}',
                    ),
                ),
            ],
        )

        # resume with user confirmation result
        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        # Verify events for tool call 1 after resumption
        expected_events = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                "mock_user_confirm_concurrent_tool",
                "User confirm concurrent result: test1",
            ),
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        # The second tool call
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=self.tool_call_id_2,
                        name="mock_user_confirm_concurrent_tool",
                        input='{"input": "test2"}',
                    ),
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        expected_events = [
            *self._get_tool_result_events(
                self.tool_call_id_2,
                "mock_user_confirm_concurrent_tool",
                "User confirm concurrent result: test2",
            ),
            *self.final_text_events,
            {"type": "RUN_FINISHED", "session_id": session_id},
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
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
                        "id": self.tool_call_id_1,
                        "name": "mock_user_confirm_concurrent_tool",
                        "input": '{"input": "test1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": "mock_user_confirm_concurrent_tool",
                        "input": '{"input": "test2"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_user_confirm_concurrent_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "User confirm concurrent result: "
                                "test1",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_user_confirm_concurrent_tool",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "User confirm concurrent result: "
                                "test2",
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Result 1",
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
