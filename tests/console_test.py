# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the interactive console cancellation boundary."""

import asyncio
from collections.abc import AsyncGenerator
from io import StringIO
from typing import cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from rich.console import Console

from agentscope.agent import Agent
from agentscope.console import ConsoleRenderer
from agentscope.console._console import _run_reply
from agentscope.event import (
    AgentEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)
from agentscope.message import ToolResultState, UserMsg


class _BlockingAgent:
    """Agent stub that waits until its reply consumer is cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def reply_stream(
        self,
        _inputs: object,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Wait indefinitely so the test can choose who cancels it."""
        yield ReplyStartEvent(
            session_id="s",
            reply_id="blocked",
            name="Agent",
        )
        yield RequireUserConfirmEvent(reply_id="blocked", tool_calls=[])
        self.started.set()
        await asyncio.Event().wait()


class _EventAgent:
    """Emit a scripted stream, optionally failing before the reply ends."""

    def __init__(self, events: list[AgentEvent | Exception]) -> None:
        self.events = events

    async def reply_stream(
        self,
        _inputs: object,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Emit events until the script ends or raises an exception."""
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


class ConsoleCancellationTest(IsolatedAsyncioTestCase):
    """Check cleanup on failure without changing cancellation or HITL."""

    def setUp(self) -> None:
        """Create a real renderer with captured terminal output."""
        self.renderer = ConsoleRenderer(console=Console(file=StringIO()))

    async def test_run_reply_propagates_caller_cancellation(self) -> None:
        """Cancelling the console task must remain visible to its caller."""
        agent = _BlockingAgent()
        task = asyncio.create_task(
            _run_reply(
                cast(Agent, agent),
                self.renderer,
                UserMsg(name="user", content="hello"),
            ),
        )
        await agent.started.wait()

        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.renderer.last_msg)
        self.assertEqual(self.renderer._active_msgs, {})

    async def test_run_reply_swallows_sigint_consumer_cancellation(
        self,
    ) -> None:
        """SIGINT still interrupts only the active reply consumer."""
        agent = _BlockingAgent()
        signal_callbacks = []
        loop = asyncio.get_running_loop()

        with (
            patch.object(
                loop,
                "add_signal_handler",
                side_effect=lambda _signal, callback: signal_callbacks.append(
                    callback,
                ),
            ),
            patch.object(loop, "remove_signal_handler"),
        ):
            task = asyncio.create_task(
                _run_reply(
                    cast(Agent, agent),
                    self.renderer,
                    UserMsg(name="user", content="hello"),
                ),
            )
            await agent.started.wait()
            signal_callbacks[0]()

            self.assertIsNone(await task)
        self.assertIsNone(self.renderer.last_msg)
        self.assertEqual(self.renderer._active_msgs, {})

    async def test_failed_replies_are_released_before_reuse(self) -> None:
        """Failed streams release every participant and preserve the error."""
        failure = RuntimeError("model stream failed")
        for index in range(3):
            agent = _EventAgent(
                [
                    ReplyStartEvent(
                        session_id="s",
                        reply_id=f"leader-{index}",
                        name="Leader",
                    ),
                    ReplyStartEvent(
                        session_id="s",
                        reply_id=f"member-{index}",
                        name="Member",
                    ),
                    TextBlockStartEvent(
                        reply_id=f"member-{index}",
                        block_id="t1",
                    ),
                    TextBlockDeltaEvent(
                        reply_id=f"member-{index}",
                        block_id="t1",
                        delta="partial response",
                    ),
                    failure,
                ],
            )
            with self.assertRaises(RuntimeError) as caught:
                await _run_reply(
                    cast(Agent, agent),
                    self.renderer,
                    UserMsg(name="user", content="hello"),
                )
            self.assertIs(caught.exception, failure)
            self.assertIsNone(self.renderer.last_msg)
            self.assertEqual(self.renderer._active_msgs, {})

        agent.events = [
            ReplyStartEvent(session_id="s", reply_id="next", name="Leader"),
            ReplyEndEvent(session_id="s", reply_id="next"),
        ]
        await _run_reply(
            cast(Agent, agent),
            self.renderer,
            UserMsg(name="user", content="retry"),
        )
        self.assertEqual(self.renderer._active_msgs, {})
        self.assertEqual(self.renderer.last_msg.id, "next")

    async def test_hitl_keeps_pending_replies_across_streams(self) -> None:
        """A normal HITL pause retains both leader and member content."""
        pending = RequireUserConfirmEvent(reply_id="member", tool_calls=[])
        agent = _EventAgent(
            [
                ReplyStartEvent(
                    session_id="s",
                    reply_id="leader",
                    name="Leader",
                ),
                ToolResultStartEvent(
                    reply_id="leader",
                    tool_call_id="c1",
                    tool_call_name="TeamAssign",
                ),
                ReplyStartEvent(
                    session_id="s",
                    reply_id="member",
                    name="Member",
                ),
                TextBlockStartEvent(reply_id="member", block_id="t1"),
                TextBlockDeltaEvent(
                    reply_id="member",
                    block_id="t1",
                    delta="before",
                ),
                pending,
            ],
        )
        result = await _run_reply(
            cast(Agent, agent),
            self.renderer,
            UserMsg(name="user", content="hello"),
        )
        self.assertIs(result, pending)
        member_msg = self.renderer.last_msg
        agent.events = [
            TextBlockDeltaEvent(
                reply_id="member",
                block_id="t1",
                delta=" after",
            ),
            ReplyEndEvent(session_id="s", reply_id="member"),
            ToolResultTextDeltaEvent(
                reply_id="leader",
                tool_call_id="c1",
                delta="done",
            ),
            ToolResultEndEvent(
                reply_id="leader",
                tool_call_id="c1",
                state=ToolResultState.SUCCESS,
            ),
            ReplyEndEvent(session_id="s", reply_id="leader"),
        ]
        with patch("agentscope.message._base.logger.warning") as warning:
            result = await _run_reply(
                cast(Agent, agent),
                self.renderer,
                UserConfirmResultEvent(
                    reply_id="member",
                    confirm_results=[],
                ),
            )
        warning.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(member_msg.get_text_content(), "before after")
        self.assertEqual(self.renderer.last_msg.id, "leader")
        self.assertEqual(self.renderer._active_msgs, {})
