# -*- coding: utf-8 -*-
"""Test the goal pipeline."""
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import Msg, TextBlock, ToolCallBlock, UserMsg
from agentscope.pipeline import GoalPipeline
from agentscope.types import ReplyFinishedReason


def _verdict(passed: bool, message: str = "") -> Msg:
    """A verifier's final message carrying a structured verdict."""
    return Msg(
        name="verifier",
        content=[],
        role="assistant",
        finished_reason=ReplyFinishedReason.COMPLETED,
        structured_output={"passed": passed, "message": message},
    )


def _no_verdict() -> Msg:
    """A verifier's final message that never called the output tool."""
    return Msg(
        name="verifier",
        content=[],
        role="assistant",
        finished_reason=ReplyFinishedReason.COMPLETED,
    )


def _confirm_request() -> RequireUserConfirmEvent:
    """A tool call parked on a human."""
    return RequireUserConfirmEvent(
        reply_id="executor-reply",
        tool_calls=[
            ToolCallBlock(id="call-1", name="write_file", input="{}"),
        ],
    )


class StubAgent:
    """Replays one scripted batch of chunks per ``reply_stream`` call.

    Records what it was handed, so a test can assert the feedback and
    reminders the pipeline built reached the right agent.
    """

    def __init__(self, name: str, script: list[list[Any]]) -> None:
        """Initialize the stub with one script entry per call."""
        self.name = name
        self.script = script
        self.state = SimpleNamespace(reply_id=f"{name}-reply")
        self.received: list[Any] = []

    # pylint: disable=unused-argument
    async def reply_stream(
        self,
        inputs: Any = None,
        structured_schema: Any = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[Any, None]:
        """Yield the next batch, holding the final message back unless it
        was asked for, the way ``Agent.reply_stream`` does."""
        self.received.append(inputs)
        batch = self.script[min(len(self.received) - 1, len(self.script) - 1)]
        for chunk in batch:
            if isinstance(chunk, Msg) and not yield_final_msg:
                continue
            yield chunk


class GoalPipelineTest(IsolatedAsyncioTestCase):
    """The goal pipeline test case."""

    async def asyncSetUp(self) -> None:
        """Prepare the input every test starts from."""
        self.query = UserMsg(name="user", content="build it")

    async def _run(self, pipe: GoalPipeline, inputs: Any) -> list:
        """Drain one pipeline run into what it yielded."""
        return [chunk async for chunk in pipe.reply_stream(inputs)]

    async def test_passes_on_first_round(self) -> None:
        """A verdict that passes ends the run after one round."""
        executor = StubAgent("executor", [[TextBlock(text="done")]])
        verifier = StubAgent("verifier", [[_verdict(True)]])
        pipe = GoalPipeline(executor, verifier, goal="check it")

        yielded = await self._run(pipe, self.query)

        self.assertListEqual(
            [chunk.model_dump() for chunk in yielded],
            [
                {
                    "type": "text",
                    "text": "done",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
        )
        self.assertListEqual(executor.received, [self.query])
        self.assertListEqual(
            [msg.model_dump() for msg in verifier.received],
            [
                {
                    "name": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "check it",
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
        )

    async def test_refusal_reaches_the_executor(self) -> None:
        """A refusal is fed back verbatim and the run tries again."""
        executor = StubAgent("executor", [[], []])
        verifier = StubAgent(
            "verifier",
            [[_verdict(False, "missing tests")], [_verdict(True)]],
        )
        pipe = GoalPipeline(executor, verifier, goal="check it")

        await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 2)
        self.assertIn("missing tests", executor.received[1].get_text_content())

    async def test_stops_at_max_iters(self) -> None:
        """A verdict that never passes stops once the budget is spent."""
        executor = StubAgent("executor", [[]])
        verifier = StubAgent("verifier", [[_verdict(False, "still wrong")]])
        pipe = GoalPipeline(executor, verifier, goal="check it", max_iters=2)

        await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 2)
        self.assertEqual(len(verifier.received), 2)

    async def test_reprompts_a_verifier_that_skips_the_tool(self) -> None:
        """A final message with no verdict is not a refusal: the verifier
        is reminded rather than the executor being sent back."""
        executor = StubAgent("executor", [[]])
        verifier = StubAgent("verifier", [[_no_verdict()], [_verdict(True)]])
        pipe = GoalPipeline(executor, verifier, goal="check it")

        await self._run(pipe, self.query)

        self.assertEqual(len(verifier.received), 2)
        self.assertIn(
            "GenerateStructuredOutput",
            verifier.received[1].get_text_content(),
        )
        # A malfunction is not charged to the executor.
        self.assertEqual(len(executor.received), 1)

    async def test_parks_on_hitl_and_resumes_into_the_same_agent(
        self,
    ) -> None:
        """A confirmation request ends the stream, and the reply id sends
        the answer back to the agent that asked for it."""
        request = _confirm_request()
        executor = StubAgent("executor", [[request], []])
        verifier = StubAgent("verifier", [[_verdict(True)]])
        pipe = GoalPipeline(executor, verifier, goal="check it")

        yielded = await self._run(pipe, self.query)

        self.assertListEqual(yielded, [request])
        self.assertListEqual(verifier.received, [])

        answer = UserConfirmResultEvent(
            reply_id="executor-reply",
            confirm_results=[
                ConfirmResult(confirmed=True, tool_call=request.tool_calls[0]),
            ],
        )
        await self._run(pipe, answer)

        self.assertListEqual(executor.received, [self.query, answer])
        self.assertEqual(len(verifier.received), 1)

    async def test_resume_keeps_the_iteration_budget(self) -> None:
        """Resuming does not hand the run a fresh set of attempts.

        Round one is refused and round two parks. With a budget of two,
        the resumed round is the last one — were the budget to restart,
        the executor would be sent back a fourth time.
        """
        executor = StubAgent("executor", [[], [_confirm_request()], []])
        verifier = StubAgent(
            "verifier",
            [[_verdict(False, "no")], [_verdict(False, "still no")]],
        )
        pipe = GoalPipeline(executor, verifier, goal="check it", max_iters=2)

        await self._run(pipe, self.query)
        await self._run(
            pipe,
            UserConfirmResultEvent(
                reply_id="executor-reply",
                confirm_results=[],
            ),
        )

        self.assertEqual(len(executor.received), 3)
        self.assertEqual(len(verifier.received), 2)

    async def test_rejects_an_unknown_reply_id(self) -> None:
        """An answer belonging to neither agent is a programming error,
        not something to guess at."""
        executor = StubAgent("executor", [[]])
        verifier = StubAgent("verifier", [[_verdict(True)]])
        pipe = GoalPipeline(executor, verifier, goal="check it")

        with self.assertRaises(ValueError):
            await self._run(
                pipe,
                UserConfirmResultEvent(
                    reply_id="nobody",
                    confirm_results=[],
                ),
            )
