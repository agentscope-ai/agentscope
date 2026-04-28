# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Test mixed user confirmation and external execution in the agent."""
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
    ToolResultBlock,
    UserMsg,
    ToolResultState,
)
from agentscope.tool._types import RegisteredTool
from agentscope.event import (
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
)


class MockMixedSequentialTool(ToolBase):
    """A mock tool that requires confirmation and external execution."""

    name: str = "mock_mixed_sequential_tool"
    description: str = "A mock mixed sequential tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_external_tool: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="Mock mixed tool requires user confirmation",
            message="Mock mixed tool requires user confirmation",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[TextBlock(text=f"Mixed sequential result: {input}")],
        )


class MockMixedConcurrentTool(ToolBase):
    """A mock tool that requires confirmation and external execution."""

    name: str = "mock_mixed_concurrent_tool"
    description: str = "A mock mixed concurrent tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_external_tool: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="Mock mixed tool requires user confirmation",
            message="Mock mixed tool requires user confirmation",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[TextBlock(text=f"Mixed concurrent result: {input}")],
        )


class AgentMixTest(IsolatedAsyncioTestCase):
    """Test mixed user confirmation and external execution."""

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
        self.sequential_tool_name = "mock_mixed_sequential_tool"
        self.concurrent_tool_name = "mock_mixed_concurrent_tool"
        self.sequential_result_1 = "Mixed sequential result: test1"
        self.sequential_result_2 = "Mixed sequential result: test2"
        self.concurrent_result_1 = "Mixed concurrent result: test1"
        self.concurrent_result_2 = "Mixed concurrent result: test2"
        self.final_response_text = "Final response after mixed execution"
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
                content=[TextBlock(text=self.final_response_text)],
                is_last=False,
                usage=None,
            ),
            ChatResponse(
                content=[TextBlock(text=self.final_response_text)],
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

    def _get_require_user_confirm_event(
        self,
        reply_id: str,
        id: str,
        name: str,
        tool_input: str,
    ) -> dict:
        """Helper method to get the expected user confirmation event."""
        return {
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": reply_id,
            "tool_calls": [
                {
                    "type": "tool_call",
                    "id": id,
                    "name": name,
                    "input": tool_input,
                    "state": "asking",
                },
            ],
        }

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

    def _get_tool_call_block(
        self,
        id: str,
        name: str,
        tool_input: str,
    ) -> ToolCallBlock:
        """Build a tool call block."""
        return ToolCallBlock(id=id, name=name, input=tool_input)

    def _get_tool_result_block(
        self,
        id: str,
        name: str,
        result: str,
        state: ToolResultState = ToolResultState.SUCCESS,
    ) -> ToolResultBlock:
        """Build a tool result block."""
        return ToolResultBlock(
            id=id,
            name=name,
            output=[TextBlock(text=result)],
            state=state,
        )

    def _get_confirm_result(
        self,
        id: str,
        name: str,
        tool_input: str,
    ) -> ConfirmResult:
        """Build a confirmation result."""
        return ConfirmResult(
            confirmed=True,
            tool_call=self._get_tool_call_block(id, name, tool_input),
        )

    def _build_tool_calls(
        self,
        tool_calls: list[tuple[str, str, str]],
    ) -> list[ToolCallBlock]:
        """Build tool call blocks for mock model responses."""
        return [
            self._get_tool_call_block(id, name, tool_input)
            for id, name, tool_input in tool_calls
        ]

    def _set_model_tool_call_responses(
        self,
        tool_calls: list[tuple[str, str, str]],
    ) -> None:
        """Set mock model responses that emit tool calls then final text."""
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=self._build_tool_calls(tool_calls),
                        is_last=False,
                        usage=None,
                    ),
                    ChatResponse(
                        content=self._build_tool_calls(tool_calls),
                        is_last=True,
                        usage=None,
                    ),
                ],
                self.final_mock_responses,
            ],
        )

    async def test_single_user_confirmation_and_external_execution(
        self,
    ) -> None:
        """Test one tool call that needs confirmation and external
        execution."""
        mixed_tool = MockMixedSequentialTool()
        self.agent.toolkit.tools[mixed_tool.name] = RegisteredTool(
            tool=mixed_tool,
            group="basic",
        )

        self._set_model_tool_call_responses(
            [
                (
                    self.tool_call_id_1,
                    self.sequential_tool_name,
                    self.tool_input_1,
                ),
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
            self._get_require_user_confirm_event(
                reply_id,
                self.tool_call_id_1,
                self.sequential_tool_name,
                self.tool_input_1,
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
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "asking",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                self._get_confirm_result(
                    self.tool_call_id_1,
                    self.sequential_tool_name,
                    self.tool_input_1,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        expected_events_resume = self._get_require_external_execution_events(
            reply_id,
            self.tool_call_id_1,
            self.sequential_tool_name,
            self.tool_input_1,
        )
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_resume],
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

        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                self._get_tool_result_block(
                    self.tool_call_id_1,
                    self.sequential_tool_name,
                    self.sequential_result_1,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(event.model_dump())

        expected_events_after_result = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.sequential_result_1,
            ),
            *self.final_text_events,
            {"type": "REPLY_END", "session_id": session_id},
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_after_result],
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

    async def test_sequential_user_confirmation_and_external_execution(
        self,
    ) -> None:
        """Test sequential tool calls that need confirmation and external
        execution."""
        mixed_tool = MockMixedSequentialTool()
        self.agent.toolkit.tools[mixed_tool.name] = RegisteredTool(
            tool=mixed_tool,
            group="basic",
        )

        self._set_model_tool_call_responses(
            [
                (
                    self.tool_call_id_1,
                    self.sequential_tool_name,
                    self.tool_input_1,
                ),
                (
                    self.tool_call_id_2,
                    self.sequential_tool_name,
                    self.tool_input_2,
                ),
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
            self._get_require_user_confirm_event(
                reply_id,
                self.tool_call_id_1,
                self.sequential_tool_name,
                self.tool_input_1,
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
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "asking",
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

        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                self._get_confirm_result(
                    self.tool_call_id_1,
                    self.sequential_tool_name,
                    self.tool_input_1,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        expected_events_resume = self._get_require_external_execution_events(
            reply_id,
            self.tool_call_id_1,
            self.sequential_tool_name,
            self.tool_input_1,
        )
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_resume],
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

        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                self._get_tool_result_block(
                    self.tool_call_id_1,
                    self.sequential_tool_name,
                    self.sequential_result_1,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(event.model_dump())

        expected_events_after_first_result = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.sequential_result_1,
            ),
            self._get_require_user_confirm_event(
                reply_id,
                self.tool_call_id_2,
                self.sequential_tool_name,
                self.tool_input_2,
            ),
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_after_first_result],
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
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_1,
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": self.sequential_tool_name,
                        "input": self.tool_input_2,
                        "state": "asking",
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
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                self._get_confirm_result(
                    self.tool_call_id_2,
                    self.sequential_tool_name,
                    self.tool_input_2,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        expected_events_after_second_confirm = (
            self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_2,
                self.sequential_tool_name,
                self.tool_input_2,
            )
        )
        self.assertListEqual(
            events,
            [
                {**basic_dict, **_}
                for _ in expected_events_after_second_confirm
            ],
        )

        external_result_event = ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[
                self._get_tool_result_block(
                    self.tool_call_id_2,
                    self.sequential_tool_name,
                    self.sequential_result_2,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(event.model_dump())

        expected_events_after_second_result = [
            *self._get_tool_result_events(
                self.tool_call_id_2,
                self.sequential_result_2,
            ),
            *self.final_text_events,
            {"type": "REPLY_END", "session_id": session_id},
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_after_second_result],
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

    async def test_concurrent_user_confirmation_and_external_execution(
        self,
    ) -> None:
        """Test concurrent tool calls that need confirmation and external
        execution."""
        mixed_tool = MockMixedConcurrentTool()
        self.agent.toolkit.tools[mixed_tool.name] = RegisteredTool(
            tool=mixed_tool,
            group="basic",
        )

        self._set_model_tool_call_responses(
            [
                (
                    self.tool_call_id_1,
                    self.concurrent_tool_name,
                    self.tool_input_1,
                ),
                (
                    self.tool_call_id_2,
                    self.concurrent_tool_name,
                    self.tool_input_2,
                ),
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
            self._get_require_user_confirm_event(
                reply_id,
                self.tool_call_id_1,
                self.concurrent_tool_name,
                self.tool_input_1,
            ),
            self._get_require_user_confirm_event(
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
                        "state": "asking",
                    },
                    {
                        "type": "tool_call",
                        "id": self.tool_call_id_2,
                        "name": self.concurrent_tool_name,
                        "input": self.tool_input_2,
                        "state": "asking",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                self._get_confirm_result(
                    self.tool_call_id_1,
                    self.concurrent_tool_name,
                    self.tool_input_1,
                ),
                self._get_confirm_result(
                    self.tool_call_id_2,
                    self.concurrent_tool_name,
                    self.tool_input_2,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(event=user_confirm_event):
            events.append(event.model_dump())

        self.assertEqual(len(events), 4)
        expected_tool_events = {
            self.tool_call_id_1: [
                {**basic_dict, **_}
                for _ in self._get_require_external_execution_events(
                    reply_id,
                    self.tool_call_id_1,
                    self.concurrent_tool_name,
                    self.tool_input_1,
                )
            ],
            self.tool_call_id_2: [
                {**basic_dict, **_}
                for _ in self._get_require_external_execution_events(
                    reply_id,
                    self.tool_call_id_2,
                    self.concurrent_tool_name,
                    self.tool_input_2,
                )
            ],
        }
        for tool_call_id, expected_tool_event in expected_tool_events.items():
            self.assertListEqual(
                [
                    event
                    for event in events
                    if event.get("tool_call_id") == tool_call_id
                    or (
                        event["type"] == "REQUIRE_EXTERNAL_EXECUTION"
                        and event["tool_calls"][0]["id"] == tool_call_id
                    )
                ],
                expected_tool_event,
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
                self._get_tool_result_block(
                    self.tool_call_id_1,
                    self.concurrent_tool_name,
                    self.concurrent_result_1,
                ),
                self._get_tool_result_block(
                    self.tool_call_id_2,
                    self.concurrent_tool_name,
                    self.concurrent_result_2,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(
            event=external_result_event,
        ):
            events.append(event.model_dump())

        expected_events_after_result = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
                self.concurrent_result_1,
            ),
            *self._get_tool_result_events(
                self.tool_call_id_2,
                self.concurrent_result_2,
            ),
            *self.final_text_events,
            {"type": "REPLY_END", "session_id": session_id},
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_after_result],
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
