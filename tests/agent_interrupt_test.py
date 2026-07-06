# -*- coding: utf-8 -*-
"""Tests for the agent interruption mechanism."""
# pylint: disable=protected-access
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    ReplyEndEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolCallState,
    ToolResultState,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.tool import Toolkit


class AgentInterruptTest(IsolatedAsyncioTestCase):
    """Test agent interruption logic."""

    async def test_handle_interruption_patches_unmatched_tool_calls(
        self,
    ) -> None:
        """_handle_interruption should patch all tool_calls without
        matching tool_results in the last AssistantMsg."""
        model = MockModel(model="mock", stream=False)
        model.set_responses(
            ChatResponse(
                content=[TextBlock(text="hello")],
                is_last=True,
            ),
        )
        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=model,
        )
        # Simulate an assistant msg with an unmatched tool call
        tc = ToolCallBlock(
            id="tc-1",
            name="test_tool",
            input='{"input": "x"}',
            state=ToolCallState.ALLOWED,
        )
        agent.state.context.append(
            AssistantMsg(
                id="reply-1",
                name="TestAgent",
                content=[TextBlock(text="Let me help."), tc],
            ),
        )

        events = []
        async for evt in agent._close_unfinished_tool_calls():
            events.append(evt)

        # Should have yielded ReplyEndEvent with interrupted reason
        reply_end = events[0]
        self.assertIsInstance(reply_end, ReplyEndEvent)
        self.assertEqual(reply_end.finished_reason, "interrupted")

        # Tool call should now be FINISHED with a matching result
        self.assertEqual(tc.state, ToolCallState.FINISHED)
        last_msg = agent._get_last_msg()
        results = last_msg.get_content_blocks("tool_result")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "tc-1")
        self.assertIn(
            "The tool call is interrupted by the user",
            str(results[0].output),
        )

    async def test_handle_interruption_noop_when_all_matched(self) -> None:
        """_handle_interruption should not add results for already-matched
        tool calls."""
        model = MockModel(model="mock", stream=False)
        model.set_responses(
            ChatResponse(
                content=[TextBlock(text="hello")],
                is_last=True,
            ),
        )
        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=model,
        )
        tc = ToolCallBlock(
            id="tc-1",
            name="test_tool",
            input='{"input": "x"}',
            state=ToolCallState.FINISHED,
        )
        tr = ToolResultBlock(
            id="tc-1",
            name="test_tool",
            output="done",
            state=ToolResultState.SUCCESS,
        )
        agent.state.context.append(
            AssistantMsg(
                id="reply-1",
                name="TestAgent",
                content=[TextBlock(text="Let me help."), tc, tr],
            ),
        )

        events = []
        async for evt in agent._close_unfinished_tool_calls():
            events.append(evt)

        reply_end = events[0]
        self.assertEqual(reply_end.finished_reason, "interrupted")
        # No duplicate result should have been added
        last_msg = agent._get_last_msg()
        results = last_msg.get_content_blocks("tool_result")
        self.assertEqual(len(results), 1)

    async def test_handle_interruption_patches_asking_tool_calls(
        self,
    ) -> None:
        """_handle_interruption should patch ASKING-state tool calls
        with interrupted results."""
        model = MockModel(model="mock", stream=False)
        model.set_responses(
            ChatResponse(
                content=[TextBlock(text="hello")],
                is_last=True,
            ),
        )
        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=model,
        )

        # Put an unmatched tool call in context
        tc = ToolCallBlock(
            id="tc-1",
            name="test_tool",
            input='{"input": "x"}',
            state=ToolCallState.ASKING,
        )
        agent.state.context.append(
            AssistantMsg(
                id="reply-1",
                name="TestAgent",
                content=[TextBlock(text="Need confirm."), tc],
            ),
        )

        # Simulate CancelledError landing during reply — call
        # _handle_interruption directly to verify cleanup.
        events = []
        async for evt in agent._close_unfinished_tool_calls():
            events.append(evt)

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ReplyEndEvent)
        self.assertEqual(events[0].finished_reason, "interrupted")

        # Verify context was patched
        self.assertEqual(tc.state, ToolCallState.FINISHED)
        last_msg = agent._get_last_msg()
        results = last_msg.get_content_blocks("tool_result")
        self.assertEqual(len(results), 1)

    async def test_interrupted_in_cycle_after_is_interrupted_response(
        self,
    ) -> None:
        """When ChatResponse.is_interrupted=True, the agent should
        set _interrupted_in_cycle and exit instead of producing a
        final AssistantMsg."""
        model = MockModel(model="mock", stream=True)
        interrupted_resp = ChatResponse(
            content=[TextBlock(text="partial...")],
            is_last=True,
            is_interrupted=True,
        )
        model.set_responses([[interrupted_resp]])

        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(),
        )

        msgs = []
        reply_ended = False
        async for evt in agent.reply_stream(
            UserMsg(name="user", content="Hi"),
        ):
            msgs.append(evt)
            if isinstance(evt, ReplyEndEvent):
                reply_ended = evt.finished_reason == "interrupted"
            # No AssistantMsg should appear
            from agentscope.message import Msg

            self.assertNotIsInstance(evt, Msg)

        self.assertTrue(
            reply_ended,
            "Should have received interrupted ReplyEndEvent",
        )

    async def test_interrupted_in_cycle_triggers_interruption_exit(
        self,
    ) -> None:
        """When _interrupted_in_cycle becomes True during reasoning
        (via is_interrupted on ChatResponse), the agent should exit
        via _handle_interruption with an unmatched tool call patched."""
        # pylint: disable=protected-access
        model = MockModel(model="mock", stream=True)
        # Model returns a tool call AND is_interrupted=True, simulating
        # a cancellation that lands during streaming after tool call
        # content was already produced.
        interrupted_tool_resp = ChatResponse(
            content=[
                TextBlock(text="Let me check."),
                ToolCallBlock(
                    id="tc-interrupt",
                    name="mock_sequential_tool",
                    input='{"input": "x"}',
                ),
            ],
            is_last=True,
            is_interrupted=True,
        )
        model.set_responses([[interrupted_tool_resp]])

        from agent_basic_test import MockSequentialTool

        toolkit = Toolkit(tools=[MockSequentialTool()])

        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=toolkit,
        )

        events = []
        reply_ended = False
        async for evt in agent.reply_stream(
            UserMsg(name="user", content="Hi"),
        ):
            events.append(evt)
            if isinstance(evt, ReplyEndEvent):
                reply_ended = evt.finished_reason == "interrupted"

        self.assertTrue(
            reply_ended,
            "Interrupted reasoning with tool calls should produce "
            "finished_reason='interrupted'",
        )

        # The unmatched tool call should have been patched
        last_msg = agent._get_last_msg()
        results = last_msg.get_content_blocks("tool_result")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "tc-interrupt")

    async def test_normal_completion_has_completed_reason(self) -> None:
        """Normal agent reply should have finished_reason='completed'."""
        model = MockModel(model="mock", stream=True)
        normal_resp = ChatResponse(
            content=[TextBlock(text="All done!")],
            is_last=True,
        )
        model.set_responses([[normal_resp]])

        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(),
        )

        reply_ended = None
        async for evt in agent.reply_stream(
            UserMsg(name="user", content="Hi"),
        ):
            if isinstance(evt, ReplyEndEvent):
                reply_ended = evt

        self.assertIsNotNone(reply_ended)
        self.assertEqual(reply_ended.finished_reason, "completed")

    async def test_stale_user_confirm_event_treated_as_noop(self) -> None:
        """After an interrupt patches ASKING tool calls to FINISHED, a
        stale UserConfirmResultEvent should be treated as a no-op
        (return False) instead of raising ValueError."""
        model = MockModel(model="mock", stream=True)
        normal_resp = ChatResponse(
            content=[TextBlock(text="Recovered.")],
            is_last=True,
        )
        model.set_responses([[normal_resp]])

        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(),
        )

        # Simulate post-interrupt state: tool call is FINISHED with
        # an interrupted result already patched.
        tc = ToolCallBlock(
            id="tc-stale",
            name="test_tool",
            input='{"input": "x"}',
            state=ToolCallState.FINISHED,
        )
        tr = ToolResultBlock(
            id="tc-stale",
            name="test_tool",
            output="<system-reminder>The tool call is interrupted "
            "by the user.</system-reminder>",
            state=ToolResultState.INTERRUPTED,
        )
        agent.state.context.append(
            AssistantMsg(
                id="reply-stale",
                name="TestAgent",
                content=[TextBlock(text="Let me help."), tc, tr],
            ),
        )

        # Build a stale UserConfirmResultEvent referencing the
        # already-patched tool call.
        stale_event = UserConfirmResultEvent(
            reply_id="reply-stale",
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id="tc-stale",
                        name="test_tool",
                        input='{"input": "x"}',
                        state=ToolCallState.ASKING,
                    ),
                ),
            ],
        )

        # Should NOT raise ValueError — instead, start a fresh
        # reasoning cycle.
        events = []
        async for evt in agent.reply_stream(inputs=stale_event):
            events.append(evt)

        # Should produce a normal completed reply (fresh cycle).
        reply_ends = [e for e in events if isinstance(e, ReplyEndEvent)]
        self.assertTrue(len(reply_ends) > 0)
        self.assertEqual(reply_ends[-1].finished_reason, "completed")
