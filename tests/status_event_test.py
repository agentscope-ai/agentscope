# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test the general-purpose status event mechanism."""
import asyncio
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.model import StructuredResponse
from agentscope.agent import Agent, ContextConfig
from agentscope.state import AgentState
from agentscope.message import UserMsg, AssistantMsg
from agentscope.tool import Toolkit
from agentscope.event import EventType, StatusEvent


class StatusEventTest(IsolatedAsyncioTestCase):
    """Test the general-purpose status event mechanism."""

    async def test_emit_status_is_noop_without_active_stream(self) -> None:
        """``emit_status`` is a no-op when no reply stream is draining."""
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=MockModel(),
            toolkit=Toolkit(),
        )
        # Should not raise even though nothing is consuming the events.
        await agent.emit_status(name="custom_op", status="start")

    async def test_drain_status_events_forwards_custom_events(self) -> None:
        """``_drain_status_events`` surfaces events emitted via
        ``emit_status`` while the awaitable runs."""
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=MockModel(),
            toolkit=Toolkit(),
        )

        async def _operation() -> None:
            await agent.emit_status(
                name="custom_op",
                status="start",
                data={"progress": 0},
            )
            await agent.emit_status(
                name="custom_op",
                status="end",
                data={"progress": 100},
            )

        events = []
        async for evt in agent._drain_status_events(_operation()):
            events.append(evt)

        self.assertEqual(len(events), 2)
        self.assertTrue(all(isinstance(e, StatusEvent) for e in events))
        self.assertEqual(events[0].name, "custom_op")
        self.assertEqual(events[0].status, "start")
        self.assertEqual(events[0].data, {"progress": 0})
        self.assertEqual(events[1].status, "end")
        self.assertEqual(events[1].data, {"progress": 100})
        # The queue should be cleared after draining.
        self.assertIsNone(agent._status_queue)

    async def test_drain_status_events_cancels_on_early_close(self) -> None:
        """Closing the generator early cancels the background operation and
        restores the status queue (no orphaned task / queue contamination)."""
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=MockModel(),
            toolkit=Toolkit(),
        )

        end_emitted = False

        async def _long_operation() -> None:
            nonlocal end_emitted
            await agent.emit_status(name="op", status="start")
            await asyncio.sleep(10)  # Simulate a long-running operation.
            end_emitted = True
            await agent.emit_status(name="op", status="end")

        gen = agent._drain_status_events(_long_operation())
        first = await gen.__anext__()
        self.assertEqual(first.status, "start")

        # Abandon the stream before the operation finishes.
        await gen.aclose()

        self.assertIsNone(agent._status_queue)
        self.assertFalse(end_emitted)

    async def test_drain_status_events_propagates_exception(self) -> None:
        """Exceptions from the wrapped awaitable propagate to the caller."""
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=MockModel(),
            toolkit=Toolkit(),
        )

        async def _failing() -> None:
            await agent.emit_status(name="custom_op", status="start")
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            async for _ in agent._drain_status_events(_failing()):
                pass
        # The queue should still be restored after the failure.
        self.assertIsNone(agent._status_queue)

    async def test_compression_emits_status_events(self) -> None:
        """Context compaction emits start/end status events when triggered."""
        model = MockModel(context_size=100)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.7,
                reserve_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    AssistantMsg(
                        "Friday",
                        "".join(["2" for _ in range(10 * 4)]),
                        id="2",
                    ),
                    UserMsg(
                        "User",
                        "".join(["3" for _ in range(10 * 4)]),
                        id="3",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        model.set_structured_response(
            StructuredResponse(
                content={
                    "task_overview": "1",
                    "current_state": "2",
                    "important_discoveries": "3",
                    "next_steps": "4",
                    "context_to_preserve": "5",
                },
            ),
        )

        events = []
        async for evt in agent._drain_status_events(agent.compress_context()):
            events.append(evt)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].type, EventType.STATUS)
        self.assertEqual(events[0].name, "compressing_context")
        self.assertEqual(events[0].status, "start")
        self.assertEqual(events[1].name, "compressing_context")
        self.assertEqual(events[1].status, "end")

    async def test_compression_skipped_emits_no_status(self) -> None:
        """No status events are emitted when compression is not triggered."""
        model = MockModel(context_size=100000)
        agent = Agent(
            name="Friday",
            system_prompt="short prompt",
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.7,
                reserve_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                context=[UserMsg("User", "hello", id="1")],
            ),
            toolkit=Toolkit(),
        )

        events = []
        async for evt in agent._drain_status_events(agent.compress_context()):
            events.append(evt)

        self.assertEqual(events, [])
