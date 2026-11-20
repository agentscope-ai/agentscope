# -*- coding: utf-8 -*-
"""Offline acceptance and ownership tests for competing agent replies."""
import asyncio
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from pipeline_team_test import AskTool, _agent, _text, _tool_call
from pipeline_team_cleanup_test import CleanupTool
from utils import MockModel

from agentscope.message import AssistantMsg, UserMsg
from agentscope.pipeline import RacePipeline
from agentscope.model import ChatResponse
from agentscope.message import Msg
from agentscope.types import ReplyFinishedReason


class GatedModel(MockModel):
    """Complete after an explicit event, or fail after signaling entry."""

    def __init__(
        self,
        gate: asyncio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        """Configure deterministic model entry and completion."""
        super().__init__()
        self.gate = gate
        self.error = error
        self.started = asyncio.Event()

    async def _call_api(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Record entry and wait for the test-controlled outcome."""
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return await super()._call_api(*args, **kwargs)


class RacePipelineTest(IsolatedAsyncioTestCase):
    """Use real agents and tools for cancellation and parked-reply tests."""

    async def test_failure_does_not_beat_valid_result(self) -> None:
        """Failure does not beat valid result."""
        failed, good = _agent("failed"), _agent("good")
        failure = RuntimeError("offline provider failed")
        failed.model = GatedModel(error=failure)
        gate = asyncio.Event()
        good.model = GatedModel(gate=gate)
        good.model.set_responses([_text("answer")])
        task = asyncio.create_task(
            RacePipeline([failed, good]).reply(UserMsg("user", "query")),
        )
        await failed.model.started.wait()
        await good.model.started.wait()
        self.assertFalse(task.done())
        gate.set()
        result = await asyncio.wait_for(task, 5)
        self.assertEqual(result.name, "good")
        self.assertEqual(result.finished_reason, ReplyFinishedReason.COMPLETED)

    async def test_rejected_result_and_stable_tie(self) -> None:
        """Rejected result and stable tie."""
        agents = [_agent("first"), _agent("second")]
        for agent in agents:
            agent.model.set_responses([_text(agent.name)])
        race = RacePipeline(agents, accept=lambda msg: msg.name == "second")
        result = await race.reply(UserMsg("user", "query"))
        self.assertEqual(result.name, "second")
        for agent in agents:
            agent.model.set_responses([_text(agent.name)])
        result = await RacePipeline(agents).reply(UserMsg("user", "again"))
        self.assertEqual(result.name, "first")

    async def test_winner_joins_active_tool_cleanup(self) -> None:
        """Winner joins active tool cleanup."""
        tool = CleanupTool()
        loser = _agent("slow", [tool])
        loser.model.set_responses([_tool_call("slow", "{}", "slow_tool")])
        winner = _agent("winner")
        winner.model = GatedModel(gate=tool.started)
        winner.model.set_responses([_text("done")])
        result = await asyncio.wait_for(
            RacePipeline([loser, winner]).reply(UserMsg("user", "go")),
            5,
        )
        self.assertEqual(result.name, "winner")
        self.assertTrue(tool.stopped.is_set())
        self.assertFalse(loser.state.get_unfinished_tool_calls(loser.name))

    async def test_caller_cancellation_joins_tools_and_allows_reuse(
        self,
    ) -> None:
        """Caller cancellation joins tools and allows reuse."""
        tool = CleanupTool()
        agent = _agent("worker", [tool])
        agent.model.set_responses([_tool_call("slow", "{}", "slow_tool")])
        race = RacePipeline([agent])
        task = asyncio.create_task(race.reply(UserMsg("user", "go")))
        await asyncio.wait_for(tool.started.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        self.assertTrue(tool.stopped.is_set())
        self.assertFalse(agent.state.get_unfinished_tool_calls(agent.name))
        agent.model.set_responses([_text("again")])
        result = await race.reply(UserMsg("user", "again"))
        self.assertEqual(result.name, "worker")

    async def test_parked_candidate_is_aborted_without_executing_tool(
        self,
    ) -> None:
        """Parked candidate is aborted without executing tool."""
        paused = _agent("paused", [AskTool()])
        paused.model.set_responses(
            [
                _tool_call("ask", '{"input":"private"}', "ask_tool"),
            ],
        )
        race = RacePipeline([paused])
        with self.assertRaises(ExceptionGroup):
            await race.reply(UserMsg("user", "go"))
        self.assertFalse(paused.state.get_unfinished_tool_calls(paused.name))

    async def test_inputs_are_copied_and_admission_is_bounded(self) -> None:
        """Inputs are copied and admission is bounded."""
        agents = [_agent(str(i)) for i in range(3)]
        started = asyncio.Queue()
        release = asyncio.Event()
        seen = []

        async def reply(inputs: Msg) -> Msg:
            """Wait while retaining the independent input copy."""
            seen.append(inputs)
            await started.put(True)
            await release.wait()
            inputs.content.clear()
            return AssistantMsg(
                "candidate",
                "ok",
                finished_reason=ReplyFinishedReason.COMPLETED,
            )

        for agent in agents:
            agent.reply = AsyncMock(side_effect=reply)
        original = UserMsg("user", "untouched")
        race = RacePipeline(agents, max_concurrency=2)
        task = asyncio.create_task(race.reply(original))
        await asyncio.wait_for(started.get(), 5)
        await asyncio.wait_for(started.get(), 5)
        self.assertEqual(len(seen), 2)
        self.assertIsNot(seen[0], seen[1])
        with self.assertRaises(RuntimeError):
            await race.reply(original)
        release.set()
        await asyncio.wait_for(task, 5)
        self.assertTrue(original.content)

    async def test_all_failures_are_reported_in_participant_order(
        self,
    ) -> None:
        """All failures are reported in participant order."""
        agents = [_agent("one"), _agent("two")]
        errors = [ValueError("one"), RuntimeError("two")]
        for agent, error in zip(agents, errors):
            agent.reply = AsyncMock(side_effect=error)
        with self.assertRaises(ExceptionGroup) as caught:
            await RacePipeline(agents).reply(UserMsg("user", "go"))
        self.assertEqual(list(caught.exception.exceptions), errors)

    async def test_predicate_error_is_not_hidden_by_another_candidate(
        self,
    ) -> None:
        """Predicate error is not hidden by another candidate."""
        agents = [_agent("one"), _agent("two")]
        for agent in agents:
            agent.model.set_responses([_text("answer")])

        def broken(_: Msg) -> bool:
            """Model a broken application-level acceptance rule."""
            raise LookupError("invalid acceptance policy")

        with self.assertRaisesRegex(LookupError, "invalid acceptance"):
            await RacePipeline(agents, accept=broken).reply(UserMsg("u", "go"))

    async def test_stream_yields_only_selected_final_message(self) -> None:
        """Stream yields only selected final message."""
        agent = _agent("worker")
        agent.model.set_responses([_text("answer")])
        items = [
            item
            async for item in RacePipeline([agent]).reply_stream(
                UserMsg("user", "go"),
            )
        ]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "worker")

    async def test_non_boolean_predicate_aborts_and_closes_coroutine(
        self,
    ) -> None:
        """An accidentally async predicate is not a truthy acceptance."""
        agent = _agent("worker")
        agent.model.set_responses([_text("answer")])
        predicate = AsyncMock(return_value=True)
        with self.assertRaisesRegex(TypeError, "synchronously"):
            await RacePipeline([agent], accept=predicate).reply(
                UserMsg("user", "go"),
            )
        predicate.assert_called_once()
        predicate.assert_not_awaited()

    async def test_repeated_cancel_does_not_abandon_async_cleanup(
        self,
    ) -> None:
        """A second caller cancellation waits for already-running cleanup."""
        agent = _agent("worker")
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def reply(_: Msg) -> Msg:
            """Model asynchronous cleanup of an owned operation."""
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                finished.set()
            raise AssertionError("unreachable")

        agent.reply = AsyncMock(side_effect=reply)
        race = RacePipeline([agent])
        task = asyncio.create_task(race.reply(UserMsg("user", "go")))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        await asyncio.wait_for(cleaning.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        self.assertTrue(finished.is_set())

    def test_configuration_rejects_shared_agents_and_invalid_limits(
        self,
    ) -> None:
        """Configuration rejects shared agents and invalid limits."""
        a = _agent("same")
        for agents, concurrency in (
            ([], 1),
            ([a, a], 2),
            ([a], 0),
            ([a], True),
        ):
            with self.subTest(agents=agents, concurrency=concurrency):
                with self.assertRaises(ValueError):
                    RacePipeline(agents, max_concurrency=concurrency)
