# -*- coding: utf-8 -*-
"""Regression tests for terminal reply events on agent failures."""
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import ReplyEndEvent, ReplyStartEvent
from agentscope.message import AssistantMsg, UserMsg
from agentscope.tool import Toolkit
from agentscope.types import ReplyFinishedReason


class AgentReplyErrorTest(IsolatedAsyncioTestCase):
    """Ensure a started reply always reaches a terminal event."""

    async def test_model_error_emits_reply_end_before_propagating(
        self,
    ) -> None:
        """A model failure must terminate the live reply before surfacing."""
        model = MockModel()
        model.max_retries = 0
        model.set_responses([RuntimeError("model backend unavailable")])
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=model,
            toolkit=Toolkit(),
        )

        events = []
        with self.assertRaisesRegex(RuntimeError, "model backend unavailable"):
            async for event in agent.reply_stream(
                UserMsg(name="user", content="Hello"),
            ):
                events.append(event)

        self.assertEqual(
            len([e for e in events if isinstance(e, ReplyStartEvent)]),
            1,
        )
        end_events = [e for e in events if isinstance(e, ReplyEndEvent)]
        self.assertEqual(len(end_events), 1)
        self.assertEqual(
            end_events[0].finished_reason,
            ReplyFinishedReason.ERROR,
        )

        reply = AssistantMsg(
            id=end_events[0].reply_id,
            name="Friday",
            content=[],
        )
        reply.append_event(end_events[0])
        self.assertEqual(reply.finished_at, end_events[0].created_at)
        self.assertEqual(reply.finished_reason, ReplyFinishedReason.ERROR)
