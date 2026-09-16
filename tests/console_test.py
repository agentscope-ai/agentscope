# -*- coding: utf-8 -*-
"""Tests for the interactive console cancellation boundary."""

import asyncio
from collections.abc import AsyncGenerator
from typing import cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import Agent
from agentscope.console import ConsoleRenderer
from agentscope.console._console import _run_reply
from agentscope.message import UserMsg


class _BlockingAgent:
    """Agent stub that waits until its reply consumer is cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def reply_stream(
        self,
        _inputs: object,
    ) -> AsyncGenerator[None, None]:
        """Wait indefinitely so the test can choose who cancels it."""
        self.started.set()
        while True:
            await asyncio.Event().wait()
            yield None


class _Renderer:
    """Renderer stub for a reply stream that never yields an event."""

    def render(self, _event: object) -> None:
        """Accept an event without producing terminal output."""
        del _event


class ConsoleCancellationTest(IsolatedAsyncioTestCase):
    """Distinguish caller cancellation from the SIGINT reply interrupt."""

    async def test_run_reply_propagates_caller_cancellation(self) -> None:
        """Cancelling the console task must remain visible to its caller."""
        agent = _BlockingAgent()
        task = asyncio.create_task(
            _run_reply(
                cast(Agent, agent),
                cast(ConsoleRenderer, _Renderer()),
                UserMsg(name="user", content="hello"),
            ),
        )
        await agent.started.wait()

        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

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
                    cast(ConsoleRenderer, _Renderer()),
                    UserMsg(name="user", content="hello"),
                ),
            )
            await agent.started.wait()
            signal_callbacks[0]()

            self.assertIsNone(await task)
