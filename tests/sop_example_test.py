# -*- coding: utf-8 -*-
"""Smoke check for the SOP example - no API key, no network.

Swaps the real models for scripted ones and drives the demo's own SOP,
verifiers and all, so the example cannot rot without a test noticing.
"""
import asyncio
import json
import os
import sys
import tempfile
from unittest import IsolatedAsyncioTestCase

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from agentscope.sop import (
    SOPEngine,
    SOPRunStatus,
    SOPStepState,
    SubmitStepResult,
    VerificationRecord,
)
from agentscope.workspace import LocalWorkspace

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "examples", "sop"),
)

from tests.utils import MockModel  # noqa: E402

import main as demo  # noqa: E402


def _submits(text: str) -> ChatResponse:
    """A reply that calls the submit tool."""
    return ChatResponse(
        content=[
            ToolCallBlock(
                id="c1",
                name=SubmitStepResult.name,
                input=json.dumps({"result": text}),
            ),
        ],
        is_last=True,
    )


def _says(text: str) -> ChatResponse:
    """A reply that just talks."""
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


class SOPExampleTest(IsolatedAsyncioTestCase):
    """The example's own SOP, driven end to end."""

    async def test_the_demo_sop_runs_and_waits_for_a_supervisor(self) -> None:
        """Three agents, a records audit, and a sign-off that parks it."""
        here = os.path.join(
            os.path.dirname(__file__),
            "..",
            "examples",
            "sop",
        )
        async with LocalWorkspace(workdir=here) as workspace:
            sop = await demo.build_sop(workspace, "unused", "unused")

            support = MockModel(context_size=200_000)
            policy = MockModel(context_size=200_000)
            writer = MockModel(context_size=200_000)
            judge = MockModel(context_size=200_000)
            support.set_responses(
                [_submits("A-1043 is 17 days late."), _says("ok")],
            )
            policy.set_responses(
                [_submits("Full refund plus a 50 coupon."), _says("ok")],
            )
            writer.set_responses(
                [_submits("很抱歉，我们将全额退款。"), _says("ok")],
            )
            # The two model-backed gates both consult this one.
            judge.set_responses([_says("PASS"), _says("PASS")])

            sop.steps[0].agent.model = support
            sop.steps[1].agent.model = policy
            sop.steps[2].agent.model = writer
            sop.steps[0].verifier._model = judge
            sop.steps[2].verifier._model = judge

            engine = SOPEngine(sop)
            async for _ in engine.run_stream([TextBlock(text="订单 A-1043")]):
                pass

            # The audit read the real records and passed; the proposal now
            # waits on a person, so the run is parked rather than settled.
            self.assertEqual(
                SOPStepState.COMPLETED,
                engine.run.steps["establish"].state,
            )
            self.assertEqual(
                SOPStepState.VERIFYING,
                engine.run.steps["propose"].state,
            )

            # A supervisor approves, and the run finishes.
            sop.steps[1].verifier.answer = VerificationRecord(
                passed=True,
                verified_by="supervisor",
            )
            async for _ in engine.run_stream():
                pass

            self.assertIs(SOPRunStatus.COMPLETED, engine.status)
            self.assertIn("退款", engine.run.steps["reply"].submission)

            context = " ".join(
                m.get_text_content() or ""
                for m in sop.steps[2].agent.state.context
            )
            self.assertIn("Full refund", context)


if __name__ == "__main__":
    case = SOPExampleTest()
    asyncio.run(case.test_the_demo_sop_runs_and_waits_for_a_supervisor())
