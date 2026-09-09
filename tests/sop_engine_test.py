# -*- coding: utf-8 -*-
"""Unittests for the SOP engine.

Driven with fake executors rather than a model: what is under test is
the engine's own reasoning — order, attempt budget, parking, and the
run state it hands out and takes back.
"""
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.event import (
    ConfirmResult,
    CustomEvent,
    UserInterruptEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import AssistantMsg, ToolCallBlock, UserMsg
from agentscope.sop import (
    SOP,
    SOPEngine,
    SOPRunState,
    SOPPhase,
    SOPStep,
)
from agentscope.types import ReplyFinishedReason


def _finished(name: str, output: dict) -> AssistantMsg:
    """A reply that ended with structured output."""
    return AssistantMsg(
        name=name,
        content="",
        finished_reason=ReplyFinishedReason.COMPLETED,
        structured_output=output,
    )


def _park() -> RequireUserConfirmEvent:
    """A reply that stopped for a person."""
    return RequireUserConfirmEvent(
        reply_id="reply-1",
        tool_calls=[
            ToolCallBlock(
                type="tool_call",
                id="call-1",
                name="shell",
                input="{}",
            ),
        ],
    )


def _answer() -> UserConfirmResultEvent:
    """What comes back when the person says yes."""
    call = ToolCallBlock(
        type="tool_call",
        id="call-1",
        name="shell",
        input="{}",
    )
    return UserConfirmResultEvent(
        reply_id="reply-1",
        confirm_results=[ConfirmResult(confirmed=True, tool_call=call)],
    )


class _Scripted:
    """An ``AgentLike`` that replays one scripted reply per call."""

    def __init__(self, name: str, script: list[list[Any]]) -> None:
        """Remember the script, and what it gets asked."""
        self.name = name
        self.script = list(script)
        self.asked: list[Any] = []

    async def reply_stream(  # pylint: disable=unused-argument
        self,
        inputs: Any = None,
        structured_schema: Any = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[Any, None]:
        """Replay the next scripted reply."""
        self.asked.append(inputs)
        for event in self.script.pop(0):
            yield event


class SOPEngineTest(IsolatedAsyncioTestCase):
    """Test the SOP engine."""

    async def _drive(self, engine: SOPEngine, inputs: Any = None) -> list:
        """Consume one ``reply_stream`` call, collecting its events."""
        return [event async for event in engine.reply_stream(inputs)]

    async def test_steps_run_in_order_and_hand_over(self) -> None:
        """A step reads what the one before handed over, and nothing else."""
        first = _Scripted("one", [[_finished("one", {"handover": "did A"})]])
        second = _Scripted("two", [[_finished("two", {"handover": "did B"})]])
        sop = SOP(
            name="demo",
            description="d",
            steps=[
                SOPStep("A", "do a", first, step_id="a"),
                SOPStep("B", "do b", second, step_id="b"),
            ],
            sop_id="sop-1",
        )
        engine = SOPEngine(sop)

        await self._drive(engine, UserMsg(name="user", content="go"))

        self.assertEqual(engine.phase, SOPPhase.COMPLETED)
        self.assertIn("did A", str(second.asked[0]))
        self.maxDiff = None
        self.assertDictEqual(
            engine.state.model_dump(exclude={"inputs"}),
            {
                "sop_id": "sop-1",
                "id": AnyString(),
                "created_at": AnyString(),
                "steps": {
                    "a": {
                        "step_id": "a",
                        "phase": SOPPhase.COMPLETED,
                        "given": [
                            {
                                "name": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "go",
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                    },
                                ],
                                "role": "user",
                                "id": AnyString(),
                                "metadata": {},
                                "created_at": AnyString(),
                                "usage": None,
                                "finished_at": AnyString(),
                                "finished_reason": None,
                                "structured_output": None,
                                "error": None,
                            },
                        ],
                        "submission": [
                            {
                                "type": "text",
                                "text": "did A",
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                        ],
                        "verifications": [
                            {
                                "passed": True,
                                "message": "",
                                "verifier": "",
                                "created_at": AnyString(),
                            },
                        ],
                    },
                    "b": {
                        "step_id": "b",
                        "phase": SOPPhase.COMPLETED,
                        "given": [
                            {
                                "name": "sop",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": '<handover from="A">',
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                    },
                                    {
                                        "type": "text",
                                        "text": "did A",
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                    },
                                    {
                                        "type": "text",
                                        "text": "</handover>",
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                    },
                                ],
                                "role": "user",
                                "id": AnyString(),
                                "metadata": {},
                                "created_at": AnyString(),
                                "usage": None,
                                "finished_at": AnyString(),
                                "finished_reason": None,
                                "structured_output": None,
                                "error": None,
                            },
                        ],
                        "submission": [
                            {
                                "type": "text",
                                "text": "did B",
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                        ],
                        "verifications": [
                            {
                                "passed": True,
                                "message": "",
                                "verifier": "",
                                "created_at": AnyString(),
                            },
                        ],
                    },
                },
            },
        )

    async def test_refusal_comes_back_as_a_critique(self) -> None:
        """A refused attempt is retried, told what was wrong and which try."""
        executor = _Scripted(
            "ex",
            [
                [_finished("ex", {"handover": "first draft"})],
                [_finished("ex", {"handover": "fixed draft"})],
            ],
        )
        verifier = _Scripted(
            "ve",
            [
                [
                    _finished(
                        "ve",
                        {"passed": False, "message": "amount wrong"},
                    ),
                ],
                [_finished("ve", {"passed": True})],
            ],
        )
        sop = SOP(
            name="demo",
            description="d",
            steps=[SOPStep("A", "do a", executor, verifier, step_id="a")],
            sop_id="sop-1",
        )
        engine = SOPEngine(sop)

        await self._drive(engine, UserMsg(name="user", content="go"))

        self.assertEqual(engine.phase, SOPPhase.COMPLETED)
        self.assertIn("amount wrong", str(executor.asked[1]))
        self.assertIn("attempt 2 of 3", str(executor.asked[1]))
        self.assertListEqual(
            [_.model_dump() for _ in engine.state.steps["a"].verifications],
            [
                {
                    "passed": False,
                    "message": "amount wrong",
                    "verifier": "ve",
                    "created_at": AnyString(),
                },
                {
                    "passed": True,
                    "message": "",
                    "verifier": "ve",
                    "created_at": AnyString(),
                },
            ],
        )

    async def test_run_fails_when_the_attempts_run_out(self) -> None:
        """Exhausting the budget fails the step, and the run with it."""
        executor = _Scripted(
            "ex",
            [[_finished("ex", {"handover": "nope"})] for _ in range(2)],
        )
        verifier = _Scripted(
            "ve",
            [
                [_finished("ve", {"passed": False, "message": "no"})]
                for _ in range(2)
            ],
        )
        sop = SOP(
            name="demo",
            description="d",
            steps=[
                SOPStep(
                    "A",
                    "do a",
                    executor,
                    verifier,
                    step_id="a",
                    max_attempts=2,
                ),
                SOPStep("B", "do b", _Scripted("two", []), step_id="b"),
            ],
            sop_id="sop-1",
        )
        engine = SOPEngine(sop)

        await self._drive(engine, UserMsg(name="user", content="go"))

        self.assertEqual(engine.phase, SOPPhase.FAILED)
        self.assertEqual(engine.state.steps["a"].phase, SOPPhase.FAILED)
        # The step behind a failure was never reached.
        self.assertEqual(engine.state.steps["b"].phase, SOPPhase.PENDING)

    async def test_parking_in_the_executor_ends_the_stream(self) -> None:
        """A parked run lets go, and picks up where it stopped."""
        executor = _Scripted(
            "ex",
            [[_park()], [_finished("ex", {"handover": "did A"})]],
        )
        verifier = _Scripted("ve", [[_finished("ve", {"passed": True})]])
        sop = SOP(
            name="demo",
            description="d",
            steps=[SOPStep("A", "do a", executor, verifier, step_id="a")],
            sop_id="sop-1",
        )
        engine = SOPEngine(sop)

        events = await self._drive(engine, UserMsg(name="user", content="go"))

        self.assertEqual(engine.phase, SOPPhase.AWAITING)
        self.assertListEqual(
            [
                (_.name, _.value) if isinstance(_, CustomEvent) else type(_)
                for _ in events
            ],
            [
                ("SOP_STEP_STARTED", {"step_id": "a", "attempt": 1}),
                RequireUserConfirmEvent,
                ("SOP_STEP_ENDED", {"step_id": "a", "phase": "awaiting"}),
            ],
        )

        await self._drive(engine, _answer())

        self.assertEqual(engine.phase, SOPPhase.COMPLETED)
        # The answer went straight to the executor, not wrapped in a brief.
        self.assertIsInstance(executor.asked[1], UserConfirmResultEvent)

    async def test_parking_in_the_verifier_does_not_redo_the_work(
        self,
    ) -> None:
        """Resuming a judged step resumes the judging, not the work."""
        executor = _Scripted("ex", [[_finished("ex", {"handover": "did A"})]])
        verifier = _Scripted(
            "ve",
            [[_park()], [_finished("ve", {"passed": True})]],
        )
        sop = SOP(
            name="demo",
            description="d",
            steps=[SOPStep("A", "do a", executor, verifier, step_id="a")],
            sop_id="sop-1",
        )
        engine = SOPEngine(sop)

        await self._drive(engine, UserMsg(name="user", content="go"))
        self.assertEqual(engine.phase, SOPPhase.AWAITING)

        await self._drive(engine, _answer())

        self.assertEqual(engine.phase, SOPPhase.COMPLETED)
        self.assertEqual(len(executor.asked), 1)
        self.assertEqual(len(verifier.asked), 2)

    async def test_the_verifier_sees_what_the_step_was_given(self) -> None:
        """Judging a submission means seeing what was asked for, too."""
        executor = _Scripted(
            "ex",
            [[_finished("ex", {"handover": "a draft"})]],
        )
        verifier = _Scripted("ve", [[_finished("ve", {"passed": True})]])
        sop = SOP(
            name="demo",
            description="d",
            steps=[SOPStep("A", "do a", executor, verifier, step_id="a")],
            sop_id="sop-1",
        )
        engine = SOPEngine(sop)

        await self._drive(engine, UserMsg(name="user", content="the ask"))

        asked = str(verifier.asked[0])
        self.assertIn("the ask", asked)
        self.assertIn("a draft", asked)

    async def test_a_run_survives_the_process_that_started_it(self) -> None:
        """Export a parked run, rebuild the SOP, and carry on."""
        sop = SOP(
            name="demo",
            description="d",
            steps=[
                SOPStep(
                    "A",
                    "do a",
                    _Scripted("ex", [[_park()]]),
                    _Scripted("ve", []),
                    step_id="a",
                ),
            ],
            sop_id="sop-1",
        )
        engine = SOPEngine(sop)
        await self._drive(engine, UserMsg(name="user", content="go"))
        stored = engine.state.model_dump_json()

        revived = SOP(
            name="demo",
            description="d",
            steps=[
                SOPStep(
                    "A",
                    "do a",
                    _Scripted(
                        "ex",
                        [[_finished("ex", {"handover": "did A"})]],
                    ),
                    _Scripted("ve", [[_finished("ve", {"passed": True})]]),
                    step_id="a",
                ),
            ],
            sop_id="sop-1",
        )
        engine2 = SOPEngine(revived, SOPRunState.model_validate_json(stored))

        self.assertEqual(engine2.phase, SOPPhase.AWAITING)
        self.assertEqual(engine2.state.id, engine.state.id)

        await self._drive(engine2, _answer())

        self.assertEqual(engine2.phase, SOPPhase.COMPLETED)

    async def test_a_run_from_another_sop_is_refused(self) -> None:
        """Loading somebody else's run is an error, not a surprise."""
        sop = SOP(
            name="demo",
            description="d",
            steps=[SOPStep("A", "do a", _Scripted("ex", []), step_id="a")],
            sop_id="sop-1",
        )
        with self.assertRaises(ValueError) as ctx:
            SOPEngine(sop, SOPRunState(sop_id="other"))
        self.assertEqual(
            str(ctx.exception),
            "State belongs to SOP other, not sop-1.",
        )

    async def test_an_interrupt_abandons_the_attempt_without_charging_it(
        self,
    ) -> None:
        """Interrupting a parked step closes its reply and stops the run.

        The attempt is abandoned, not refused: no verdict is filed, the
        budget is untouched, and the step waits at PENDING for a fresh
        start rather than being retried on the spot.
        """
        executor = _Scripted("ex", [[_park()], []])
        sop = SOP(
            name="demo",
            description="d",
            steps=[SOPStep("A", "do a", executor, step_id="a")],
            sop_id="sop-1",
        )
        engine = SOPEngine(sop)
        await self._drive(engine, UserMsg(name="user", content="go"))
        self.assertEqual(engine.phase, SOPPhase.AWAITING)

        events = await self._drive(
            engine,
            UserInterruptEvent(reply_id="reply-1"),
        )

        self.assertIsInstance(executor.asked[1], UserInterruptEvent)
        self.assertEqual(len(executor.asked), 2)
        self.assertDictEqual(
            engine.state.steps["a"].model_dump(),
            {
                "step_id": "a",
                "phase": SOPPhase.PENDING,
                # What the abandoned attempt was dispatched with stays on
                # record; the next dispatch overwrites it.
                "given": [
                    {
                        "name": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "go",
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                        ],
                        "role": "user",
                        "id": AnyString(),
                        "metadata": {},
                        "created_at": AnyString(),
                        "usage": None,
                        "finished_at": AnyString(),
                        "finished_reason": None,
                        "structured_output": None,
                        "error": None,
                    },
                ],
                "submission": None,
                "verifications": [],
            },
        )
        self.assertListEqual(
            [(_.name, _.value) for _ in events if isinstance(_, CustomEvent)],
            [
                ("SOP_STEP_STARTED", {"step_id": "a", "attempt": 1}),
                ("SOP_STEP_ENDED", {"step_id": "a", "phase": "pending"}),
            ],
        )
