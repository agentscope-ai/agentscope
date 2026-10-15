# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Test the user confirmation events in the agent class."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString, MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.model import ChatResponse
from agentscope.tool import (
    ToolBase,
    Toolkit,
    ToolChunk,
)
from agentscope.permission import (
    PermissionDecision,
    PermissionBehavior,
    PermissionContext,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.event import UserConfirmResultEvent, ConfirmResult


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


class MockUserConfirmConcurrentToolB(ToolBase):
    """A second concurrent confirm tool with a distinct name.

    Used to exercise concurrent confirmations that are NOT de-duplicated:
    two calls to *different* tools never share a suggested rule, so both
    surface their own confirmation prompt.
    """

    name: str = "mock_user_confirm_concurrent_tool_b"
    description: str = "A second mock user confirm concurrent tool"
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
                TextBlock(text=f"User confirm concurrent result B: {input}"),
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
            # The runtime state injection is covered by
            # agent_injection_test, turn it off to keep the assertions
            # focused.
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        self.tool_call_id_1 = "tool_call_1"
        self.tool_call_id_2 = "tool_call_2"
        self.user_input_text = "Test"
        self.tool_input_1 = '{"input": "test1"}'
        self.tool_input_2 = '{"input": "test2"}'
        self.sequential_tool_name = "mock_user_confirm_sequential_tool"
        self.concurrent_tool_name = "mock_user_confirm_concurrent_tool"
        self.sequential_result_1 = "User confirm sequential result: test1"
        self.sequential_result_2 = "User confirm sequential result: test2"
        self.concurrent_result_1 = "User confirm concurrent result: test1"
        self.concurrent_result_2 = "User confirm concurrent result: test2"
        self.final_response_text = "Result 1"
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
                "text": None,
            },
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "finished_reason": "completed",
            },
        ]

        self.final_mock_responses = [
            ChatResponse(
                content=[
                    TextBlock(text=self.final_response_text),
                ],
                is_last=False,
            ),
            ChatResponse(
                content=[
                    TextBlock(text=self.final_response_text),
                ],
                is_last=True,
            ),
        ]

    def _get_event_base(self, reply_id: str) -> dict:
        """Get the dict with the basic fields for event assertion."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "metadata": {},
            "reply_id": reply_id,
        }

    def _get_msg_base(self) -> dict:
        """Get the dict with the basic fields for message assertion."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "finished_at": None,
            "finished_reason": None,
            "structured_output": None,
            "error": None,
            "metadata": {},
            "name": "Friday",
            "role": "assistant",
            "usage": None,
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
        self.agent.toolkit = Toolkit(
            tools=[confirm_tool],
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
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        # First call: collect events until REQUIRE_USER_CONFIRM
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
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "finished_reason": "completed",
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "asking",
                        "suggested_rules": [],
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
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": self.user_input_text,
                    },
                ],
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "asking",
                        "suggested_rules": [],
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
                        name=self.sequential_tool_name,
                        input=self.tool_input_1,
                    ),
                ),
            ],
        )

        # Second call: resume with user confirmation result
        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        # Verify events after resumption
        expected_events_resume = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.sequential_tool_name,
                self.sequential_result_1,
            ),
            *self.final_text_events,
            {
                "type": "REPLY_END",
                "error": None,
                "session_id": session_id,
                "finished_reason": "completed",
            },
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
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": self.user_input_text,
                    },
                ],
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "finished",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_result",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "name": self.sequential_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "created_at": AnyString(),
                                "finished_at": None,
                                "id": AnyString(),
                                "text": self.sequential_result_1,
                            },
                        ],
                        "state": "success",
                        "metadata": {},
                    },
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
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
        self.agent.toolkit = Toolkit(
            tools=[confirm_tool],
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

        # First call: collect events until REQUIRE_USER_CONFIRM
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
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "finished_reason": "completed",
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "asking",
                        "suggested_rules": [],
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
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": self.user_input_text,
                    },
                ],
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "asking",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_2,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_2,
                        "state": "pending",
                        "suggested_rules": [],
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
                        name=self.sequential_tool_name,
                        input=self.tool_input_1,
                    ),
                ),
            ],
        )

        # resume with user confirmation result
        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        # Verify events after resumption (sequential execution)
        expected_events_resume = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.sequential_tool_name,
                self.sequential_result_1,
            ),
            {
                "type": "REQUIRE_USER_CONFIRM",
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_2,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_2,
                        "state": "asking",
                        "suggested_rules": [],
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
                        id=self.tool_call_id_2,
                        name=self.sequential_tool_name,
                        input=self.tool_input_2,
                    ),
                ),
            ],
        )

        # Second call: resume with user confirmation result
        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        expected_events_resume_2 = [
            *self._get_tool_result_events(
                self.tool_call_id_2,
                self.sequential_tool_name,
                self.sequential_result_2,
            ),
            *self.final_text_events,
            {
                "type": "REPLY_END",
                "error": None,
                "session_id": session_id,
                "finished_reason": "completed",
            },
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
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": self.user_input_text,
                    },
                ],
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "finished",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_2,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_2,
                        "state": "finished",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_result",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "name": self.sequential_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "created_at": AnyString(),
                                "finished_at": None,
                                "id": AnyString(),
                                "text": self.sequential_result_1,
                            },
                        ],
                        "state": "success",
                        "metadata": {},
                    },
                    {
                        "type": "tool_result",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "name": self.sequential_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "created_at": AnyString(),
                                "finished_at": None,
                                "id": AnyString(),
                                "text": self.sequential_result_2,
                            },
                        ],
                        "state": "success",
                        "metadata": {},
                    },
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
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

    async def test_concurrent_user_confirmation(self) -> None:
        """Concurrent confirmations, each confirmed WITHOUT a rule.

        Base tools suggest no permission rules, so batch de-duplication
        does not apply: each concurrent call surfaces its own confirmation
        and the user explicitly approves both. The agent should:
        1. Generate two concurrent tool calls that require confirmation
        2. Emit TWO REQUIRE_USER_CONFIRM events (one per call) and pause
        3. On confirming both, execute them and continue
        4. Both calls complete; neither is silently skipped
        """
        # Register user confirm concurrent tool
        confirm_tool = MockUserConfirmConcurrentTool()
        self.agent.toolkit = Toolkit(
            tools=[confirm_tool],
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
                    ),
                ],
                self.final_mock_responses,
            ],
        )

        # First call: collect events until REQUIRE_USER_CONFIRM
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

        # Both calls surface their own confirmation; with no suggested
        # rules there is nothing to de-duplicate against.
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
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "finished_reason": "completed",
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_1,
                        "state": "asking",
                        "suggested_rules": [],
                    },
                ],
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_2,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_2,
                        "state": "asking",
                        "suggested_rules": [],
                    },
                ],
            },
        ]

        basic_dict = self._get_event_base(reply_id)
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        # Assert context after first run: both tool calls asking, each with
        # no suggested rules.
        msg_base = self._get_msg_base()
        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": self.user_input_text,
                    },
                ],
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_1,
                        "state": "asking",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_2,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_2,
                        "state": "asking",
                        "suggested_rules": [],
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Confirm both calls (no rules to accept).
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=self.tool_call_id_1,
                        name=self.concurrent_tool_name,
                        input=self.tool_input_1,
                    ),
                ),
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=self.tool_call_id_2,
                        name=self.concurrent_tool_name,
                        input=self.tool_input_2,
                    ),
                ),
            ],
        )

        # resume with user confirmation result
        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        # Both calls execute; the reply completes.
        expected_events = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.concurrent_tool_name,
                self.concurrent_result_1,
            ),
            *self._get_tool_result_events(
                self.tool_call_id_2,
                self.concurrent_tool_name,
                self.concurrent_result_2,
            ),
            *self.final_text_events,
            {
                "type": "REPLY_END",
                "error": None,
                "session_id": session_id,
                "finished_reason": "completed",
            },
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
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": self.user_input_text,
                    },
                ],
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_1,
                        "state": "finished",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_2,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_2,
                        "state": "finished",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_result",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "name": self.concurrent_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "created_at": AnyString(),
                                "finished_at": None,
                                "id": AnyString(),
                                "text": self.concurrent_result_1,
                            },
                        ],
                        "state": "success",
                        "metadata": {},
                    },
                    {
                        "type": "tool_result",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "name": self.concurrent_tool_name,
                        "output": [
                            {
                                "type": "text",
                                "created_at": AnyString(),
                                "finished_at": None,
                                "id": AnyString(),
                                "text": self.concurrent_result_2,
                            },
                        ],
                        "state": "success",
                        "metadata": {},
                    },
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
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

    async def test_concurrent_user_confirmation_no_silent_auto_allow(self) -> None:
        """Confirming one call never silently auto-runs a concurrent call.

        Regression test for https://github.com/agentscope-ai/agentscope/issues/3115:
        base tools suggest no permission rules, so each concurrent call
        surfaces its own confirmation. Confirming the first call (with no
        rules to accept) must not allow the second unseen call — it gets
        its own prompt.
        """
        confirm_tool = MockUserConfirmConcurrentTool()
        self.agent.toolkit = Toolkit(
            tools=[confirm_tool],
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
                        is_last=True,
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

        reply_id = self.agent.state.reply_id

        # Run 1: both calls surface their own confirmation — with no
        # suggested rules there is nothing to de-duplicate against.
        confirm_events = [
            _ for _ in events if _["type"] == "REQUIRE_USER_CONFIRM"
        ]
        self.assertEqual(len(confirm_events), 2)
        self.assertEqual(
            [c["tool_calls"][0]["id"] for c in confirm_events],
            [self.tool_call_id_1, self.tool_call_id_2],
        )
        for confirm_event in confirm_events:
            self.assertEqual(
                confirm_event["tool_calls"][0]["suggested_rules"], []
            )

        # Confirm the first call (no rules are suggested, so none are
        # accepted).
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=self.tool_call_id_1,
                        name=self.concurrent_tool_name,
                        input=self.tool_input_1,
                    ),
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        # The first call executes; the second was shown to the user but not
        # confirmed, so it does not execute — and crucially, it is not
        # silently auto-allowed either.
        finished_ids = [
            _["tool_call_id"] for _ in events if _["type"] == "TOOL_RESULT_END"
        ]
        self.assertEqual(finished_ids, [self.tool_call_id_1])

        # The second call is still waiting for the user's decision.
        assistant_msg = self.agent.state.context[-1]
        states = {
            _.model_dump()["id"]: _.model_dump()["state"]
            for _ in assistant_msg.get_content_blocks("tool_call")
        }
        self.assertEqual(states[self.tool_call_id_1], "finished")
        self.assertIn(states[self.tool_call_id_2], ["asking", "pending"])

        # Confirm the second call; it executes and the reply completes.
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=self.tool_call_id_2,
                        name=self.concurrent_tool_name,
                        input=self.tool_input_2,
                    ),
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        finished_ids = sorted(
            _["tool_call_id"] for _ in events if _["type"] == "TOOL_RESULT_END"
        )
        self.assertEqual(finished_ids, [self.tool_call_id_2])
        self.assertEqual(events[-1]["type"], "REPLY_END")

        # Both tool calls end up finished.
        assistant_msg = self.agent.state.context[-1]
        self.assertEqual(
            [
                _.model_dump()["state"]
                for _ in assistant_msg.get_content_blocks("tool_call")
            ],
            ["finished", "finished"],
        )

    async def test_concurrent_user_confirmation_in_single_event(self) -> None:
        """Two different-tool confirmations resolved by one event.

        Two concurrent calls to *different* tools do not share a suggested
        rule, so neither is de-duplicated and both surface a confirmation. A
        single UserConfirmResultEvent then carries both approvals and both
        tools execute on resume. The agent should:
        1. Generate two concurrent tool calls (distinct tools) that require
           confirmation
        2. Emit two REQUIRE_USER_CONFIRM events and pause
        3. Resume when one UserConfirmResultEvent carries both confirmations
        4. Execute both tools and continue reasoning after both complete
        """
        name_a = self.concurrent_tool_name
        name_b = "mock_user_confirm_concurrent_tool_b"
        result_a = self.concurrent_result_1
        result_b = "User confirm concurrent result B: test2"
        self.agent.toolkit = Toolkit(
            tools=[
                MockUserConfirmConcurrentTool(),
                MockUserConfirmConcurrentToolB(),
            ],
        )

        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=name_a,
                                input=self.tool_input_1,
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name=name_b,
                                input=self.tool_input_2,
                            ),
                        ],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=self.tool_call_id_1,
                                name=name_a,
                                input=self.tool_input_1,
                            ),
                            ToolCallBlock(
                                id=self.tool_call_id_2,
                                name=name_b,
                                input=self.tool_input_2,
                            ),
                        ],
                        is_last=True,
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
            name_a,
            self.tool_input_1,
        )
        tool_call_2_events = self._get_tool_call_events(
            self.tool_call_id_2,
            name_b,
            self.tool_input_2,
        )

        rule_a = []
        rule_b = []

        # Distinct tools do not de-duplicate: both surface a confirmation.
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
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "finished_reason": "completed",
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": name_a,
                        "input": self.tool_input_1,
                        "state": "asking",
                        "suggested_rules": rule_a,
                    },
                ],
            },
            {
                "type": "REQUIRE_USER_CONFIRM",
                "reply_id": reply_id,
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_2,
                        "name": name_b,
                        "input": self.tool_input_2,
                        "state": "asking",
                        "suggested_rules": rule_b,
                    },
                ],
            },
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": self.user_input_text,
                    },
                ],
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_1,
                        "name": name_a,
                        "input": self.tool_input_1,
                        "state": "asking",
                        "suggested_rules": rule_a,
                    },
                    {
                        "type": "tool_call",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": self.tool_call_id_2,
                        "name": name_b,
                        "input": self.tool_input_2,
                        "state": "asking",
                        "suggested_rules": rule_b,
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # A single confirmation event carrying BOTH approvals.
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=self.tool_call_id_1,
                        name=name_a,
                        input=self.tool_input_1,
                    ),
                ),
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=self.tool_call_id_2,
                        name=name_b,
                        input=self.tool_input_2,
                    ),
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        tool_events = events[:6]
        final_events = events[6:]
        self.assertEqual(len(tool_events), 6)

        expected_tool_events = {
            self.tool_call_id_1: [
                {**basic_dict, **_}
                for _ in self._get_tool_result_events(
                    self.tool_call_id_1,
                    name_a,
                    result_a,
                )
            ],
            self.tool_call_id_2: [
                {**basic_dict, **_}
                for _ in self._get_tool_result_events(
                    self.tool_call_id_2,
                    name_b,
                    result_b,
                )
            ],
        }
        for tool_call_id, expected_tool_event in expected_tool_events.items():
            self.assertListEqual(
                [
                    event
                    for event in tool_events
                    if event["tool_call_id"] == tool_call_id
                ],
                expected_tool_event,
            )

        expected_final_events = [
            *self.final_text_events,
            {
                "type": "REPLY_END",
                "error": None,
                "session_id": session_id,
                "finished_reason": "completed",
            },
        ]
        self.assertListEqual(
            final_events,
            [{**basic_dict, **_} for _ in expected_final_events],
        )

        self.assertEqual(len(self.agent.state.context), 2)
        self.assertEqual(
            self.agent.state.context[0].model_dump(),
            {
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "metadata": {},
                "name": "user",
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": self.user_input_text,
                    },
                ],
                "usage": None,
            },
        )

        assistant_msg = self.agent.state.context[-1]
        self.assertEqual(
            [
                _.model_dump()["state"]
                for _ in assistant_msg.get_content_blocks("tool_call")
            ],
            ["finished", "finished"],
        )
        self.assertEqual(
            [
                _.model_dump()["id"]
                for _ in assistant_msg.get_content_blocks("tool_call")
            ],
            [self.tool_call_id_1, self.tool_call_id_2],
        )
        self.assertEqual(
            {
                (
                    _.model_dump()["name"],
                    _.model_dump()["state"],
                    _.output[0].text,
                )
                for _ in assistant_msg.get_content_blocks("tool_result")
            },
            {
                (name_a, "success", result_a),
                (name_b, "success", result_b),
            },
        )
        self.assertEqual(
            [_.text for _ in assistant_msg.get_content_blocks("text")],
            [self.final_response_text],
        )

    async def test_partial_concurrent_confirmation_defers_iteration(
        self,
    ) -> None:
        """A tool round is not counted while one confirmed call is pending."""
        name_a = self.concurrent_tool_name
        name_b = "mock_user_confirm_concurrent_tool_b"
        self.agent.toolkit = Toolkit(
            tools=[
                MockUserConfirmConcurrentTool(),
                MockUserConfirmConcurrentToolB(),
            ],
        )
        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=self.tool_call_id_1,
                            name=name_a,
                            input=self.tool_input_1,
                        ),
                        ToolCallBlock(
                            id=self.tool_call_id_2,
                            name=name_b,
                            input=self.tool_input_2,
                        ),
                    ],
                    is_last=True,
                ),
                self.final_mock_responses,
            ],
        )

        parked = await self.agent.reply(
            UserMsg(name="user", content=self.user_input_text),
        )
        self.assertIsNone(parked.finished_reason)
        self.assertEqual(self.agent.state.cur_iter, 0)

        partial = await self.agent.reply(
            UserConfirmResultEvent(
                reply_id=self.agent.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=ToolCallBlock(
                            id=self.tool_call_id_1,
                            name=name_a,
                            input=self.tool_input_1,
                        ),
                    ),
                ],
            ),
        )
        self.assertIsNone(partial.finished_reason)
        # The first call has run, but the round isn't over while the second
        # one is still awaiting confirmation
        self.assertEqual(self.agent.state.cur_iter, 0)

        completed = await self.agent.reply(
            UserConfirmResultEvent(
                reply_id=self.agent.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=ToolCallBlock(
                            id=self.tool_call_id_2,
                            name=name_b,
                            input=self.tool_input_2,
                        ),
                    ),
                ],
            ),
        )
        self.assertEqual(completed.finished_reason, "completed")
        self.assertEqual(self.model.cnt, 2)
        # One tool round plus the final reasoning
        self.assertEqual(self.agent.state.cur_iter, 2)

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
