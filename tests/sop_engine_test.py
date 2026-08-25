# -*- coding: utf-8 -*-
"""End-to-end tests for the SOP engine, driven by a scripted model.

These run real agents — the engine attaches its submit tool, prompts them,
reads what they submit and hands it to the verifier — with the model's
replies scripted so the whole thing is deterministic.
"""
import json
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit
from agentscope.model import ChatResponse
from agentscope.sop import (
    SOP,
    CallbackVerifier,
    RunSettledEvent,
    SOPEngine,
    SOPRunStatus,
    SOPStep,
    SOPStepState,
    StepStateEvent,
    SubmitStepResult,
    VerificationRecord,
)

from tests.utils import MockModel


def _submits(text: str) -> ChatResponse:
    """A reply that calls the submit tool with ``text``."""
    return ChatResponse(
        content=[
            ToolCallBlock(
                id="call-1",
                name=SubmitStepResult.name,
                input=json.dumps({"result": text}),
            ),
        ],
        is_last=True,
    )


def _says(text: str) -> ChatResponse:
    """A reply that just talks, submitting nothing."""
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


def _agent(
    model: MockModel,
    name: str = "worker",
    toolkit: Toolkit | None = None,
) -> Agent:
    """An agent wired to a scripted model."""
    return Agent(
        name=name,
        system_prompt="",
        model=model,
        toolkit=toolkit or Toolkit(),
    )


class NeedsConfirming(ToolBase):
    """A tool that always stops to ask, so a step parks mid-flight."""

    name: str = "Risky"
    description: str = "Does something that needs a nod first."
    input_schema: dict = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = False
    is_read_only: bool = False

    async def call(self) -> ToolChunk:
        """Report that it ran."""
        return ToolChunk(content=[TextBlock(text="did the risky thing")])

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always ask."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Risky wants a nod.",
        )


def _calls_risky() -> ChatResponse:
    """A reply that calls the tool needing confirmation."""
    return ChatResponse(
        content=[ToolCallBlock(id="c0", name="Risky", input="{}")],
        is_last=True,
    )


class SOPEngineTest(IsolatedAsyncioTestCase):
    """The engine end to end."""

    async def test_a_two_step_run_hands_text_from_one_to_the_next(
        self,
    ) -> None:
        """The submission of the first step reaches the second's prompt."""
        first, second = MockModel(), MockModel()
        first.set_responses([_submits("the plan is X"), _says("ok")])
        second.set_responses([_submits("built it"), _says("ok")])

        sop = SOP(
            name="two",
            steps=[
                SOPStep(
                    id="a",
                    subject="Plan",
                    agent=_agent(first, "planner"),
                ),
                SOPStep(
                    id="b",
                    subject="Build",
                    agent=_agent(second, "builder"),
                    blocked_by=["a"],
                ),
            ],
        )
        engine = SOPEngine(sop)

        settled = [
            e
            async for e in engine.run_stream([TextBlock(text="do the thing")])
            if isinstance(e, RunSettledEvent)
        ]

        self.assertEqual(1, len(settled))
        self.assertEqual(SOPRunStatus.COMPLETED, settled[0].status)
        self.assertEqual("the plan is X", engine.run.steps["a"].submission)
        self.assertEqual("built it", engine.run.steps["b"].submission)

        # The second agent was told what the first handed over.
        prompt = second.formatter and str(
            [m.get_text_content() for m in sop.steps[1].agent.state.context],
        )
        self.assertIn("the plan is X", prompt)

    async def test_a_refused_step_is_told_why_and_tries_again(self) -> None:
        """The refusal reason reaches the retry, and the count is the
        verdicts."""
        model = MockModel()
        model.set_responses(
            [
                _submits("first try"),
                _says("ok"),
                _submits("second try"),
                _says("ok"),
            ],
        )

        def decide(sop, run, step, record) -> VerificationRecord:
            passed = record.submission == "second try"
            return VerificationRecord(
                passed=passed,
                message="" if passed else "say 'second try'",
            )

        sop = SOP(
            name="retry",
            steps=[
                SOPStep(
                    id="a",
                    subject="Try",
                    agent=_agent(model),
                    verifier=CallbackVerifier(decide),
                ),
            ],
        )
        engine = SOPEngine(sop)

        async for _ in engine.run_stream([TextBlock(text="go")]):
            pass

        record = engine.run.steps["a"]
        self.assertEqual(SOPStepState.COMPLETED, record.state)
        self.assertEqual(2, len(record.verifications))
        self.assertFalse(record.verifications[0].passed)
        self.assertEqual("say 'second try'", record.verifications[0].message)

        context = " ".join(
            m.get_text_content() or ""
            for m in sop.steps[0].agent.state.context
        )
        self.assertIn("say 'second try'", context)

    async def test_a_verifier_with_no_answer_ends_the_stream(self) -> None:
        """Waiting parks the run instead of spinning, and a later pass
        picks it up — no coroutine held open in between."""
        model = MockModel()
        model.set_responses([_submits("please review"), _says("ok")])
        answer: list[VerificationRecord] = []

        def decide(sop, run, step, record) -> VerificationRecord | None:
            return answer[0] if answer else None

        sop = SOP(
            name="review",
            steps=[
                SOPStep(
                    id="a",
                    subject="Review me",
                    agent=_agent(model),
                    verifier=CallbackVerifier(decide),
                ),
            ],
        )
        engine = SOPEngine(sop)

        events = [_ async for _ in engine.run_stream([TextBlock(text="go")])]

        # Parked: no settle event, and the step is still being verified.
        self.assertFalse(any(isinstance(e, RunSettledEvent) for e in events))
        self.assertEqual(SOPStepState.VERIFYING, engine.run.steps["a"].state)

        # The answer arrives; carrying on takes no new input.
        answer.append(VerificationRecord(passed=True, verified_by="lead"))
        settled = [
            e
            async for e in engine.run_stream()
            if isinstance(e, RunSettledEvent)
        ]

        self.assertEqual(SOPRunStatus.COMPLETED, settled[0].status)
        self.assertEqual(SOPStepState.COMPLETED, engine.run.steps["a"].state)

    async def test_a_failing_step_settles_the_run_and_strands_the_rest(
        self,
    ) -> None:
        """The step behind a failure never runs, and needs no state to
        say so."""
        first, second = MockModel(), MockModel()
        first.set_responses([_submits("no good"), _says("ok")])
        second.set_responses([_submits("never reached"), _says("ok")])

        sop = SOP(
            name="fails",
            steps=[
                SOPStep(
                    id="a",
                    subject="Fail",
                    agent=_agent(first, "one"),
                    verifier=CallbackVerifier(
                        lambda *_: VerificationRecord(
                            passed=False,
                            message="not acceptable",
                        ),
                    ),
                    max_attempts=1,
                ),
                SOPStep(
                    id="b",
                    subject="Never",
                    agent=_agent(second, "two"),
                    blocked_by=["a"],
                ),
            ],
        )
        engine = SOPEngine(sop)

        settled = [
            e
            async for e in engine.run_stream([TextBlock(text="go")])
            if isinstance(e, RunSettledEvent)
        ]

        self.assertEqual(SOPRunStatus.FAILED, settled[0].status)
        self.assertEqual(SOPStepState.FAILED, engine.run.steps["a"].state)
        self.assertEqual(SOPStepState.PENDING, engine.run.steps["b"].state)
        self.assertEqual("", engine.run.steps["b"].submission)

    async def test_step_events_label_the_stream(self) -> None:
        """Agent events are unreadable without knowing whose they are."""
        model = MockModel()
        model.set_responses([_submits("done"), _says("ok")])

        sop = SOP(
            name="one",
            steps=[SOPStep(id="a", subject="Only step", agent=_agent(model))],
        )
        engine = SOPEngine(sop)

        labels = [
            (e.subject, e.state)
            async for e in engine.run_stream([TextBlock(text="go")])
            if isinstance(e, StepStateEvent)
        ]

        self.assertEqual(
            [
                ("Only step", SOPStepState.RUNNING),
                ("Only step", SOPStepState.VERIFYING),
                ("Only step", SOPStepState.COMPLETED),
            ],
            labels,
        )

    async def test_an_agent_stopping_for_permission_parks_the_run(
        self,
    ) -> None:
        """The stream ends, nothing is suspended, and the answer finds
        its own way back to the agent that asked."""
        model = MockModel()
        model.set_responses(
            [_calls_risky(), _submits("did it"), _says("ok")],
        )
        toolkit = Toolkit(tools=[NeedsConfirming()])

        sop = SOP(
            name="risky",
            steps=[
                SOPStep(
                    id="a",
                    subject="Do the risky thing",
                    agent=_agent(model, toolkit=toolkit),
                ),
            ],
        )
        engine = SOPEngine(sop)

        events = [_ async for _ in engine.run_stream([TextBlock(text="go")])]
        asked = [
            e for e in events if isinstance(e, RequireUserConfirmEvent)
        ]

        self.assertEqual(1, len(asked))
        self.assertFalse(any(isinstance(e, RunSettledEvent) for e in events))
        self.assertEqual(SOPStepState.RUNNING, engine.run.steps["a"].state)

        # The answer names the reply, not the agent — the engine works out
        # who was waiting.
        answer = UserConfirmResultEvent(
            reply_id=asked[0].reply_id,
            confirm_results=[
                ConfirmResult(confirmed=True, tool_call=tc)
                for tc in asked[0].tool_calls
            ],
        )
        settled = [
            e
            async for e in engine.run_stream(answer)
            if isinstance(e, RunSettledEvent)
        ]

        self.assertEqual(SOPRunStatus.COMPLETED, settled[0].status)
        self.assertEqual("did it", engine.run.steps["a"].submission)

    async def test_preset_tasks_seed_the_agents_own_list(self) -> None:
        """A step may narrow how its agent decomposes the work."""
        from agentscope.state import Task

        model = MockModel()
        model.set_responses([_submits("done"), _says("ok")])
        agent = _agent(model)

        sop = SOP(
            name="seeded",
            steps=[
                SOPStep(
                    id="a",
                    subject="Seeded",
                    agent=agent,
                    tasks=[
                        Task(
                            subject="Read the spec",
                            description="",
                            metadata={},
                        ),
                    ],
                ),
            ],
        )
        engine = SOPEngine(sop)

        async for _ in engine.run_stream([TextBlock(text="go")]):
            pass

        seeded = agent.state.tasks_context.tasks
        self.assertEqual(1, len(seeded))
        self.assertEqual("Read the spec", seeded[0].subject)
