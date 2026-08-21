# -*- coding: utf-8 -*-
"""Smoke check for the SOP example — no API key, no network.

Swaps the real model for a scripted one and drives the demo's own SOP,
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

    async def test_the_demo_sop_runs_and_waits_for_a_person(self) -> None:
        """Two agents, a file check, and a sign-off that parks the run."""
        with tempfile.TemporaryDirectory() as workdir:
            async with LocalWorkspace(workdir=workdir) as workspace:
                sop = await demo.build_sop(workspace, "unused", "unused")
                note = os.path.join(workspace.workdir, demo.NOTE)

                # A roomy context so the demo's real prompts do not
                # trip compression, which the mock cannot script.
                writer = MockModel(context_size=200_000)
                editor = MockModel(context_size=200_000)
                writer.set_responses(
                    [
                        _submits("- audience\n- three points"),
                        _says("ok"),
                        _submits(f"Wrote {demo.NOTE}."),
                        _says("ok"),
                    ],
                )
                # Stand in for the writer actually creating it, so the
                # demo's FileWritten verifier has something real to find.
                with open(note, "w", encoding="utf-8") as fh:
                    fh.write("# Note\n")
                editor.set_responses([_submits("We shipped it."), _says("ok")])
                sop.steps[0].agent.model = writer
                sop.steps[2].agent.model = editor

                engine = SOPEngine(sop)
                async for _ in engine.run_stream([TextBlock(text="topic")]):
                    pass

                # The first two steps are through; the third waits on a
                # person, so the run is parked rather than settled.
                self.assertEqual(
                    SOPStepState.COMPLETED,
                    engine.run.steps["draft"].state,
                )
                self.assertEqual(
                    SOPStepState.VERIFYING,
                    engine.run.steps["announce"].state,
                )
                self.assertTrue(os.path.exists(note))

                # The editor has no tools, so all it saw was the text.
                self.assertIn(
                    demo.NOTE,
                    " ".join(
                        m.get_text_content() or ""
                        for m in sop.steps[2].agent.state.context
                    ),
                )

                # A verdict arrives and the run finishes.
                sop.steps[2].verifier.answer = VerificationRecord(
                    passed=True,
                    verified_by="you",
                )
                async for _ in engine.run_stream():
                    pass

                self.assertIs(SOPRunStatus.COMPLETED, engine.status)


if __name__ == "__main__":
    case = SOPExampleTest()
    asyncio.run(case.test_the_demo_sop_runs_and_waits_for_a_person())
