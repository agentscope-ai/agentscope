# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Cancellation must stop orchestration, not trigger another attempt."""
import asyncio
import json
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import ReplyEndEvent
from agentscope.message import Msg, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.pipeline import GoalPipeline
from agentscope.sop import SOP, SOPEngine, SOPPhase, SOPStep
from agentscope.types import ReplyFinishedReason


def _output(content: dict, call_id: str) -> ChatResponse:
    """Produce the structured output requested by the real agent loop."""
    return ChatResponse(
        content=[
            ToolCallBlock(
                id=call_id,
                name="GenerateStructuredOutput",
                input=json.dumps(content),
            ),
        ],
        is_last=True,
    )


class _BlockingModel(MockModel):
    """Block the first model call until the consuming task is cancelled."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def _call_api(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        response = await super()._call_api(*args, **kwargs)
        if self.cnt == 1:
            self.started.set()
            await asyncio.Event().wait()
        return response


def _agent(name: str, model: MockModel, output: dict) -> Agent:
    """Keep a successful next response available to expose unwanted retries."""
    model.set_responses([_output(output, "first"), _output(output, "second")])
    return Agent(
        name=name,
        system_prompt=f"You are {name}.",
        model=model,
        injection_config=InjectionConfig(inject_runtime_state=False),
    )


class PipelineCancellationTest(IsolatedAsyncioTestCase):
    """Exercise task.cancel() with real agents on either side of each loop."""

    async def _cancel_running(
        self,
        pipeline: SOPEngine | GoalPipeline,
        model: _BlockingModel,
    ) -> list:
        events = []

        async def consume() -> None:
            async for event in pipeline.reply_stream(UserMsg("user", "Go")):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(model.started.wait(), timeout=5)
            task.cancel()
            await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        endings = [e for e in events if isinstance(e, ReplyEndEvent)]
        self.assertEqual(
            endings[-1].finished_reason,
            ReplyFinishedReason.INTERRUPTED,
        )
        self.assertTrue(
            any(
                isinstance(e, Msg)
                and e.finished_reason == ReplyFinishedReason.INTERRUPTED
                for e in events
            ),
        )
        return events

    async def test_sop_cancellation_abandons_executor_or_verifier(
        self,
    ) -> None:
        """An interrupted attempt costs no refusal and starts no next step."""
        for participant in ("executor", "verifier"):
            with self.subTest(participant=participant):
                blocked = _BlockingModel()
                ex_model = (
                    blocked if participant == "executor" else MockModel()
                )
                ve_model = (
                    blocked if participant == "verifier" else MockModel()
                )
                executor = _agent("executor", ex_model, {"handover": "draft"})
                verifier = _agent("verifier", ve_model, {"passed": True})
                next_model = MockModel()
                next_agent = _agent("next", next_model, {"handover": "done"})
                engine = SOPEngine(
                    SOP(
                        name="cancelled run",
                        steps=[
                            SOPStep(
                                "work",
                                "Prepare draft",
                                executor,
                                verifier,
                            ),
                            SOPStep("publish", "Publish draft", next_agent),
                        ],
                    ),
                )

                await self._cancel_running(engine, blocked)

                self.assertEqual(ex_model.cnt, 1)
                self.assertEqual(ve_model.cnt, int(participant == "verifier"))
                self.assertEqual(next_model.cnt, 0)
                self.assertEqual(engine.phase, SOPPhase.PENDING)
                self.assertIsNone(engine.state.steps[0].submission)
                self.assertEqual(engine.state.steps[0].verifications, [])

                # A new explicit request may run the abandoned attempt again.
                async for _ in engine.reply_stream(UserMsg("user", "Retry")):
                    pass
                self.assertEqual(engine.phase, SOPPhase.COMPLETED)
                self.assertEqual(
                    [v.passed for v in engine.state.steps[0].verifications],
                    [True],
                )

    async def test_goal_cancellation_does_not_retry_either_participant(
        self,
    ) -> None:
        """Cancellation spends no retry or verification budget."""
        for participant in ("executor", "verifier"):
            with self.subTest(participant=participant):
                blocked = _BlockingModel()
                ex_model = (
                    blocked if participant == "executor" else MockModel()
                )
                ve_model = (
                    blocked if participant == "verifier" else MockModel()
                )
                executor = _agent("executor", ex_model, {"report": "draft"})
                verifier = _agent(
                    "verifier",
                    ve_model,
                    {"result": "pass", "message": "ok"},
                )
                pipeline = GoalPipeline(executor, verifier)

                await self._cancel_running(pipeline, blocked)

                self.assertEqual(ex_model.cnt, 1)
                self.assertEqual(ve_model.cnt, int(participant == "verifier"))
                self.assertEqual(pipeline._executor_retries, 0)
                self.assertEqual(pipeline._verifier_retries, 0)
                self.assertEqual(pipeline._iters, 0)
