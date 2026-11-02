# -*- coding: utf-8 -*-
"""Test that rounds completed during HITL resumption count into
``max_iters``.

A reasoning-acting round that parks on an external execution or a user
confirmation, and is then completed by the resuming event (an external
execution result, or denying the last remaining confirmation), must be
counted into ``cur_iter`` — mirroring the completion check the
reasoning-acting loop applies at the end of each pass. Before the fix for
the HITL resumption accounting, such rounds were never counted, so
``cur_iter`` stayed at 0 and ``max_iters`` never triggered.
"""
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChoice, ToolChunk, Toolkit
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)


class MockExternalTool(ToolBase):
    """A mock tool that requires external execution."""

    name: str = "mock_external_tool"
    description: str = "A mock external tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
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

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        raise NotImplementedError("Executed externally.")


class MockConfirmTool(ToolBase):
    """A mock tool that requires user confirmation."""

    name: str = "mock_confirm_tool"
    description: str = "A mock confirm tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
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
            decision_reason="Mock confirm tool always asks",
            message="Mock confirm tool always asks",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(content=[TextBlock(text="Confirmed result")])


class ToolChoiceRecordingModel(MockModel):
    """A mock model that records the ``tool_choice`` of each call."""

    def __init__(self) -> None:
        """Initialize the mock model and the recorded tool choices."""
        super().__init__()
        self.tool_choices: list[Any] = []

    async def __call__(
        self,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Record the tool choice and forward to the mock model."""
        self.tool_choices.append(tool_choice)
        return await super().__call__(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )


class HitlMaxItersTest(IsolatedAsyncioTestCase):
    """Test the iteration accounting of HITL-resumed rounds."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.user_input_text = "Test"
        self.final_response_text = "Final response"
        self.tool_call_id_1 = "tool_call_1"
        self.tool_call_id_2 = "tool_call_2"

    def _make_agent(
        self,
        model: MockModel,
        tool: ToolBase,
        max_iters: int,
    ) -> Agent:
        """Create an agent with the given tool and iteration budget."""
        return Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[tool]),
            react_config=ReActConfig(max_iters=max_iters),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    def _tool_call_response(
        self,
        *tool_calls: ToolCallBlock,
    ) -> ChatResponse:
        """Build a model response that only carries tool calls."""
        return ChatResponse(
            content=list(tool_calls),
            is_last=True,
            usage=None,
        )

    def _final_text_responses(self) -> list[ChatResponse]:
        """Build the streaming responses of a final text-only answer."""
        return [
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

    def _tool_call_block(self, call_id: str) -> ToolCallBlock:
        """Build a tool call block for the external/confirm mock tool."""
        return ToolCallBlock(
            id=call_id,
            name="mock_external_tool",
            input="{}",
        )

    def _external_result(
        self,
        agent: Agent,
        *tool_calls: ToolCallBlock,
    ) -> ExternalExecutionResultEvent:
        """Build a successful external execution result event."""
        return ExternalExecutionResultEvent(
            reply_id=agent.state.reply_id,
            execution_results=[
                ToolResultBlock(
                    id=tool_call.id,
                    name=tool_call.name,
                    output=[TextBlock(text="External result")],
                    state=ToolResultState.SUCCESS,
                )
                for tool_call in tool_calls
            ],
        )

    async def test_external_resumption_round_counts_toward_max_iters(
        self,
    ) -> None:
        """A round completed by an external result is counted, so the
        budget is enforced exactly like the local-tool flow."""
        model = ToolChoiceRecordingModel()
        agent = self._make_agent(model, MockExternalTool(), max_iters=1)
        model.set_responses(
            [
                self._tool_call_response(
                    self._tool_call_block(self.tool_call_id_1),
                ),
                self._final_text_responses(),
            ],
        )

        parked = await agent.reply(
            UserMsg(name="user", content=self.user_input_text),
        )
        self.assertIsNone(parked.finished_reason)
        self.assertEqual(agent.state.cur_iter, 0)

        final = await agent.reply(
            self._external_result(
                agent,
                self._tool_call_block(
                    self.tool_call_id_1,
                ),
            ),
        )
        # One parked round completed by the result, plus the forced
        # finalization reasoning: the budget stops the reply.
        self.assertEqual(final.finished_reason, "exceed_max_iters")
        self.assertEqual(agent.state.cur_iter, 2)
        self.assertEqual(model.cnt, 2)
        # The budget-exhausted call must not allow any tool.
        self.assertEqual(model.tool_choices[1].mode, "none")

    async def test_denied_confirmation_round_counts_toward_max_iters(
        self,
    ) -> None:
        """A round closed by denying the confirmation is counted, so the
        budget is enforced exactly like the local-tool flow."""
        model = ToolChoiceRecordingModel()
        agent = self._make_agent(model, MockConfirmTool(), max_iters=1)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=self.tool_call_id_1,
                            name="mock_confirm_tool",
                            input="{}",
                        ),
                    ],
                    is_last=True,
                    usage=None,
                ),
                self._final_text_responses(),
            ],
        )

        parked = await agent.reply(
            UserMsg(name="user", content=self.user_input_text),
        )
        self.assertIsNone(parked.finished_reason)
        self.assertEqual(agent.state.cur_iter, 0)

        final = await agent.reply(
            UserConfirmResultEvent(
                reply_id=agent.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=False,
                        tool_call=ToolCallBlock(
                            id=self.tool_call_id_1,
                            name="mock_confirm_tool",
                            input="{}",
                        ),
                    ),
                ],
            ),
        )
        # The denied round is completed by the event, plus the forced
        # finalization reasoning: the budget stops the reply.
        self.assertEqual(final.finished_reason, "exceed_max_iters")
        self.assertEqual(agent.state.cur_iter, 2)
        self.assertEqual(model.cnt, 2)
        # The budget-exhausted call must not allow any tool.
        self.assertEqual(model.tool_choices[1].mode, "none")

    async def test_partial_external_resumption_counts_round_once(
        self,
    ) -> None:
        """Results arriving across several events count the round exactly
        once — when the last result completes it."""
        model = MockModel()
        agent = self._make_agent(model, MockExternalTool(), max_iters=2)
        model.set_responses(
            [
                self._tool_call_response(
                    self._tool_call_block(self.tool_call_id_1),
                    self._tool_call_block(self.tool_call_id_2),
                ),
                self._final_text_responses(),
            ],
        )

        parked = await agent.reply(
            UserMsg(name="user", content=self.user_input_text),
        )
        self.assertIsNone(parked.finished_reason)
        self.assertEqual(agent.state.cur_iter, 0)

        # The first partial result leaves the round unfinished: the reply
        # parks again on the second call and nothing is counted yet.
        partial = await agent.reply(
            self._external_result(
                agent,
                self._tool_call_block(
                    self.tool_call_id_1,
                ),
            ),
        )
        self.assertIsNone(partial.finished_reason)
        self.assertEqual(agent.state.cur_iter, 0)

        # The last result completes the round: it counts once, the final
        # reasoning counts once more, and the reply completes normally.
        final = await agent.reply(
            self._external_result(
                agent,
                self._tool_call_block(
                    self.tool_call_id_2,
                ),
            ),
        )
        self.assertEqual(final.finished_reason, "completed")
        self.assertEqual(agent.state.cur_iter, 2)
        self.assertEqual(model.cnt, 2)

    async def test_allowed_confirmation_round_counted_once(self) -> None:
        """A round completed in the loop after an allowed confirmation is
        still counted exactly once (no double counting at resumption)."""
        model = MockModel()
        agent = self._make_agent(model, MockConfirmTool(), max_iters=2)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=self.tool_call_id_1,
                            name="mock_confirm_tool",
                            input="{}",
                        ),
                    ],
                    is_last=True,
                    usage=None,
                ),
                self._final_text_responses(),
            ],
        )

        parked = await agent.reply(
            UserMsg(name="user", content=self.user_input_text),
        )
        self.assertIsNone(parked.finished_reason)
        self.assertEqual(agent.state.cur_iter, 0)

        final = await agent.reply(
            UserConfirmResultEvent(
                reply_id=agent.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=ToolCallBlock(
                            id=self.tool_call_id_1,
                            name="mock_confirm_tool",
                            input="{}",
                        ),
                    ),
                ],
            ),
        )
        # The allowed call executes within the loop, so the round is
        # counted there — the resumption check must not count it again.
        self.assertEqual(final.finished_reason, "completed")
        self.assertEqual(agent.state.cur_iter, 2)
        self.assertEqual(model.cnt, 2)

    async def test_consecutive_external_resumptions_enforce_budget(
        self,
    ) -> None:
        """Several parked rounds in a row each count once, so a larger
        budget is exhausted exactly after that many rounds."""
        model = ToolChoiceRecordingModel()
        agent = self._make_agent(model, MockExternalTool(), max_iters=2)
        model.set_responses(
            [
                self._tool_call_response(
                    self._tool_call_block(self.tool_call_id_1),
                ),
                self._tool_call_response(
                    self._tool_call_block(self.tool_call_id_2),
                ),
                self._final_text_responses(),
            ],
        )

        # Round 1 parks; its result completes and counts it.
        parked = await agent.reply(
            UserMsg(name="user", content=self.user_input_text),
        )
        self.assertIsNone(parked.finished_reason)
        resumed = await agent.reply(
            self._external_result(
                agent,
                self._tool_call_block(
                    self.tool_call_id_1,
                ),
            ),
        )
        self.assertIsNone(resumed.finished_reason)
        self.assertEqual(agent.state.cur_iter, 1)

        # Round 2 parks; its result completes and counts it, which
        # exhausts the budget and forces the finalization call.
        final = await agent.reply(
            self._external_result(
                agent,
                self._tool_call_block(
                    self.tool_call_id_2,
                ),
            ),
        )
        self.assertEqual(final.finished_reason, "exceed_max_iters")
        self.assertEqual(agent.state.cur_iter, 3)
        self.assertEqual(model.cnt, 3)
        self.assertEqual(model.tool_choices[2].mode, "none")
