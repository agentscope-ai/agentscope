# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Test mixed user confirmation and external execution in the agent."""
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
    ToolResultBlock,
    UserMsg,
    ToolCallState,
    ToolResultState,
)
from agentscope.event import (
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
    ConfirmResult,
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
            # Runtime-state injection is covered by agent_injection_test.
            # Keep these assertions focused on the HITL event flow.
            injection_config=InjectionConfig(inject_runtime_state=False),
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
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "finished_reason": "completed",
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

    @staticmethod
    def _get_suggested_rules(name: str) -> list[dict]:
        """Get the rule suggested for a tool confirmation."""
        return [
            {
                "tool_name": name,
                "rule_content": None,
                "behavior": PermissionBehavior.ALLOW,
                "source": "suggested",
            },
        ]

    def _get_expected_user_message(self) -> dict:
        """Get the expected user message."""
        return {
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
        }

    def _get_expected_tool_call_block(
        self,
        id: str,
        name: str,
        tool_input: str,
        state: ToolCallState,
        *,
        has_suggested_rule: bool = True,
    ) -> dict:
        """Get the expected serialized tool call block."""
        return {
            "type": "tool_call",
            "created_at": AnyString(),
            "finished_at": None,
            "id": id,
            "name": name,
            "input": tool_input,
            "state": state,
            "suggested_rules": (
                self._get_suggested_rules(name) if has_suggested_rule else []
            ),
        }

    @staticmethod
    def _get_expected_text_block(text: str) -> dict:
        """Get the expected serialized text block."""
        return {
            "type": "text",
            "created_at": AnyString(),
            "finished_at": None,
            "id": AnyString(),
            "text": text,
        }

    def _get_expected_tool_result_block(
        self,
        name: str,
        result: str,
    ) -> dict:
        """Get the expected serialized tool result block."""
        return {
            "type": "tool_result",
            "created_at": AnyString(),
            "finished_at": None,
            "id": AnyString(),
            "name": name,
            "output": [self._get_expected_text_block(result)],
            "state": "success",
            "metadata": {},
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
                "state": "success",
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
                self._get_expected_tool_call_block(
                    id,
                    name,
                    tool_input,
                    ToolCallState.ASKING,
                ),
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
                    self._get_expected_tool_call_block(
                        id,
                        name,
                        tool_input,
                        ToolCallState.SUBMITTED,
                    ),
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
    ) -> ToolResultBlock:
        """Build a tool result block."""
        return ToolResultBlock(
            id=id,
            name=name,
            output=[TextBlock(text=result)],
            state=ToolResultState.SUCCESS,
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
        self.agent.toolkit = Toolkit(tools=[mixed_tool])

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
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "finished_reason": "completed",
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
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.sequential_tool_name,
                        self.tool_input_1,
                        ToolCallState.ASKING,
                    ),
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
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
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
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.sequential_tool_name,
                        self.tool_input_1,
                        ToolCallState.SUBMITTED,
                    ),
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
            inputs=external_result_event,
        ):
            events.append(event.model_dump())

        expected_events_after_result = [
            *self._get_tool_result_events(
                self.tool_call_id_1,
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
            [{**basic_dict, **_} for _ in expected_events_after_result],
        )

        expected_context_final = [
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.sequential_tool_name,
                        self.tool_input_1,
                        ToolCallState.FINISHED,
                    ),
                    self._get_expected_tool_result_block(
                        self.sequential_tool_name,
                        self.sequential_result_1,
                    ),
                    self._get_expected_text_block(self.final_response_text),
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
        self.agent.toolkit = Toolkit(tools=[mixed_tool])

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
                "cache_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "finished_reason": "completed",
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
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.sequential_tool_name,
                        self.tool_input_1,
                        ToolCallState.ASKING,
                    ),
                    self._get_expected_tool_call_block(
                        self.tool_call_id_2,
                        self.sequential_tool_name,
                        self.tool_input_2,
                        ToolCallState.PENDING,
                        has_suggested_rule=False,
                    ),
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
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
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
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.sequential_tool_name,
                        self.tool_input_1,
                        ToolCallState.SUBMITTED,
                    ),
                    self._get_expected_tool_call_block(
                        self.tool_call_id_2,
                        self.sequential_tool_name,
                        self.tool_input_2,
                        ToolCallState.PENDING,
                        has_suggested_rule=False,
                    ),
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
            inputs=external_result_event,
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
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.sequential_tool_name,
                        self.tool_input_1,
                        ToolCallState.FINISHED,
                    ),
                    self._get_expected_tool_call_block(
                        self.tool_call_id_2,
                        self.sequential_tool_name,
                        self.tool_input_2,
                        ToolCallState.ASKING,
                    ),
                    self._get_expected_tool_result_block(
                        self.sequential_tool_name,
                        self.sequential_result_1,
                    ),
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
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
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
            inputs=external_result_event,
        ):
            events.append(event.model_dump())

        expected_events_after_second_result = [
            *self._get_tool_result_events(
                self.tool_call_id_2,
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
            [{**basic_dict, **_} for _ in expected_events_after_second_result],
        )

        expected_context_final = [
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.sequential_tool_name,
                        self.tool_input_1,
                        ToolCallState.FINISHED,
                    ),
                    self._get_expected_tool_call_block(
                        self.tool_call_id_2,
                        self.sequential_tool_name,
                        self.tool_input_2,
                        ToolCallState.FINISHED,
                    ),
                    self._get_expected_tool_result_block(
                        self.sequential_tool_name,
                        self.sequential_result_1,
                    ),
                    self._get_expected_tool_result_block(
                        self.sequential_tool_name,
                        self.sequential_result_2,
                    ),
                    self._get_expected_text_block(self.final_response_text),
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
        """Concurrent calls confirmed one at a time, without an allow rule.

        Two concurrent calls to the same tool share one tool-name-level
        suggested rule, so batch de-duplication surfaces only the first
        confirmation and leaves the second PENDING. Confirming the first
        WITHOUT the suggested rule sends it on to external execution and
        surfaces the second call's own (deferred) prompt in the same run —
        the state this fixture exists to cover, where one call sits on the
        external gate while its peer sits on the confirmation gate.

        Confirming the first WITH the always-allow rule instead is
        ``hitl_user_confirmation_test``'s rule-dedup case, and is not
        repeated here.
        """
        mixed_tool = MockMixedConcurrentTool()
        self.agent.toolkit = Toolkit(tools=[mixed_tool])

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

        # Calls to the same tool share a suggested rule, so only the first
        # call asks for confirmation while the second remains pending.
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
            self._get_require_user_confirm_event(
                reply_id,
                self.tool_call_id_1,
                self.concurrent_tool_name,
                self.tool_input_1,
            ),
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        expected_context = [
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.concurrent_tool_name,
                        self.tool_input_1,
                        ToolCallState.ASKING,
                    ),
                    self._get_expected_tool_call_block(
                        self.tool_call_id_2,
                        self.concurrent_tool_name,
                        self.tool_input_2,
                        ToolCallState.PENDING,
                        has_suggested_rule=False,
                    ),
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Confirming the first call without a rule hands it to external
        # execution and, in the same run, surfaces the second call's own
        # deferred prompt — the two gates are open at once.
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                self._get_confirm_result(
                    self.tool_call_id_1,
                    self.concurrent_tool_name,
                    self.tool_input_1,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        expected_events_resume = [
            *self._get_require_external_execution_events(
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
            [{**basic_dict, **_} for _ in expected_events_resume],
        )

        expected_context = [
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.concurrent_tool_name,
                        self.tool_input_1,
                        ToolCallState.SUBMITTED,
                    ),
                    # The deferred prompt restores the suggested rule the
                    # PENDING placeholder above did not carry.
                    self._get_expected_tool_call_block(
                        self.tool_call_id_2,
                        self.concurrent_tool_name,
                        self.tool_input_2,
                        ToolCallState.ASKING,
                    ),
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

        # Confirming the second call sends it to the same external gate.
        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                self._get_confirm_result(
                    self.tool_call_id_2,
                    self.concurrent_tool_name,
                    self.tool_input_2,
                ),
            ],
        )

        events = []
        async for event in self.agent.reply_stream(inputs=user_confirm_event):
            events.append(event.model_dump())

        expected_events_second_confirm = (
            self._get_require_external_execution_events(
                reply_id,
                self.tool_call_id_2,
                self.concurrent_tool_name,
                self.tool_input_2,
            )
        )
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_second_confirm],
        )

        expected_context = [
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.concurrent_tool_name,
                        self.tool_input_1,
                        ToolCallState.SUBMITTED,
                    ),
                    self._get_expected_tool_call_block(
                        self.tool_call_id_2,
                        self.concurrent_tool_name,
                        self.tool_input_2,
                        ToolCallState.SUBMITTED,
                    ),
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
            inputs=external_result_event,
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
            {
                "type": "REPLY_END",
                "error": None,
                "session_id": session_id,
                "finished_reason": "completed",
            },
        ]
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events_after_result],
        )

        expected_context_final = [
            self._get_expected_user_message(),
            {
                "content": [
                    self._get_expected_tool_call_block(
                        self.tool_call_id_1,
                        self.concurrent_tool_name,
                        self.tool_input_1,
                        ToolCallState.FINISHED,
                    ),
                    self._get_expected_tool_call_block(
                        self.tool_call_id_2,
                        self.concurrent_tool_name,
                        self.tool_input_2,
                        ToolCallState.FINISHED,
                    ),
                    self._get_expected_tool_result_block(
                        self.concurrent_tool_name,
                        self.concurrent_result_1,
                    ),
                    self._get_expected_tool_result_block(
                        self.concurrent_tool_name,
                        self.concurrent_result_2,
                    ),
                    self._get_expected_text_block(self.final_response_text),
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
