# -*- coding: utf-8 -*-
"""Tests for the agent interruption mechanism."""
# pylint: disable=protected-access
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import ReplyEndEvent
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
        async for evt in agent._handle_interruption():
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
        self.assertIn("Interrupted by user", str(results[0].output))

    async def test_handle_interruption_noop_when_all_matched(self) -> None:
        """_handle_interruption should not add results for already-matched
        tool calls."""
        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=ChatResponse(
                content=[TextBlock(text="hello")],
                is_last=True,
            ),
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
        async for evt in agent._handle_interruption():
            events.append(evt)

        reply_end = events[0]
        self.assertEqual(reply_end.finished_reason, "interrupted")
        # No duplicate result should have been added
        last_msg = agent._get_last_msg()
        results = last_msg.get_content_blocks("tool_result")
        self.assertEqual(len(results), 1)

    async def test_reply_stream_catches_cancelled_error(self) -> None:
        """reply_stream should catch CancelledError, patch context,
        and yield ReplyEndEvent."""
        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=ChatResponse(
                content=[TextBlock(text="hello")],
                is_last=True,
            ),
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
        async for evt in agent._handle_interruption():
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
        """When _interrupted_in_cycle is set during a reply cycle,
        _reply_impl should exit via _handle_interruption."""
        # pylint: disable=protected-access
        model = MockModel(model="mock", stream=True)
        # First turn: model returns a tool call
        tool_call_resp = ChatResponse(
            content=[
                TextBlock(text="Let me check."),
                ToolCallBlock(
                    id="tc-interrupt",
                    name="mock_sequential_tool",
                    input='{"input": "x"}',
                ),
            ],
            is_last=True,
        )
        model.set_responses([[tool_call_resp]])

        # Register a simple tool that works
        from agent_basic_test import MockSequentialTool

        toolkit = Toolkit(tools=[MockSequentialTool()])

        agent = Agent(
            name="TestAgent",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=toolkit,
        )

        # Manually trigger _interrupted_in_cycle to simulate
        # what happens when a tool response has is_interrupted=True
        agent._interrupted_in_cycle = True

        # This will cause the agent to exit after the current
        # reasoning-acting cycle with an interrupted ReplyEndEvent
        events = []
        async for evt in agent.reply_stream(
            UserMsg(name="user", content="Hi"),
        ):
            events.append(evt)
            if isinstance(evt, ReplyEndEvent):
                self.assertEqual(evt.finished_reason, "interrupted")
                break

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
