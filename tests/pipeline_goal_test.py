# -*- coding: utf-8 -*-
"""Test the goal pipeline."""
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import Msg, ToolCallBlock, UserMsg
from agentscope.pipeline import GoalPipeline
from agentscope.types import ReplyFinishedReason


def _report(text: str = "已完成，见 main.py") -> Msg:
    """An executor's final message carrying its achievement report."""
    return Msg(
        name="executor",
        content=[],
        role="assistant",
        finished_reason=ReplyFinishedReason.COMPLETED,
        structured_output={"report": text},
    )


def _verdict(result: str, message: str = "") -> Msg:
    """A verifier's final message carrying a structured verdict."""
    return Msg(
        name="verifier",
        content=[],
        role="assistant",
        finished_reason=ReplyFinishedReason.COMPLETED,
        structured_output={"result": result, "message": message},
    )


def _no_output(name: str) -> Msg:
    """A final message from an agent that never called the output tool."""
    return Msg(
        name=name,
        content=[],
        role="assistant",
        finished_reason=ReplyFinishedReason.COMPLETED,
    )


def _confirm_request(
    reply_id: str = "executor-reply",
) -> RequireUserConfirmEvent:
    """A tool call parked on a human."""
    return RequireUserConfirmEvent(
        reply_id=reply_id,
        tool_calls=[
            ToolCallBlock(id="call-1", name="write_file", input="{}"),
        ],
    )


class StubAgent:
    """Replays one scripted batch of chunks per ``reply_stream`` call.

    Records what it was handed, so a test can assert the feedback and
    reminders the pipeline built reached the right agent.
    """

    def __init__(
        self,
        name: str,
        script: list[list[Any]],
        max_calls: int | None = None,
    ) -> None:
        """Initialize the stub with one script entry per call."""
        self.name = name
        self.script = script
        self.max_calls = max_calls
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
        if self.max_calls is not None and len(self.received) >= self.max_calls:
            raise AssertionError(f"{self.name} exceeded its call limit")
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
        self.query = UserMsg(name="user", content="写一个爬虫")

    async def _run(self, pipe: GoalPipeline, inputs: Any) -> list:
        """Drain one pipeline run into what it yielded."""
        return [chunk async for chunk in pipe.reply_stream(inputs)]

    async def test_passes_on_first_round(self) -> None:
        """A passing verdict ends the run, and the verifier is told both
        the goal and what the executor reported."""
        executor = StubAgent("executor", [[_report("见 main.py")]])
        verifier = StubAgent("verifier", [[_verdict("pass")]])
        pipe = GoalPipeline(executor, verifier)

        yielded = await self._run(pipe, self.query)

        self.assertListEqual(
            [chunk.structured_output for chunk in yielded],
            [{"report": "见 main.py"}],
        )
        told = verifier.received[0].get_text_content()
        self.assertIn("写一个爬虫", told)
        self.assertIn("见 main.py", told)

    async def test_refusal_reaches_the_executor(self) -> None:
        """A refusal is fed back verbatim and the run tries again."""
        executor = StubAgent("executor", [[_report()], [_report()]])
        verifier = StubAgent(
            "verifier",
            [[_verdict("fail", "缺 requirements.txt")], [_verdict("pass")]],
        )
        pipe = GoalPipeline(executor, verifier)

        await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 2)
        self.assertIn(
            "缺 requirements.txt",
            executor.received[1].get_text_content(),
        )

    async def test_stops_at_max_iters(self) -> None:
        """A verdict that never passes stops once the budget is spent."""
        executor = StubAgent("executor", [[_report()]])
        verifier = StubAgent("verifier", [[_verdict("fail", "还是不行")]])
        pipe = GoalPipeline(executor, verifier, max_iters=2)

        await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 2)
        self.assertEqual(len(verifier.received), 2)

    async def test_impossible_ends_the_run(self) -> None:
        """An impossible goal settles the run rather than retrying."""
        executor = StubAgent("executor", [[_report()]])
        verifier = StubAgent(
            "verifier",
            [[_verdict("impossible", "目标自相矛盾")]],
        )
        pipe = GoalPipeline(executor, verifier)

        await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 1)
        self.assertEqual(len(verifier.received), 1)

    async def test_executor_stops_after_structured_output_retries(
        self,
    ) -> None:
        """An executor cannot retry a missing report indefinitely."""
        executor = StubAgent(
            "executor",
            [[_no_output("executor")]],
            max_calls=2,
        )
        verifier = StubAgent("verifier", [[_verdict("pass")]])
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        with self.assertRaisesRegex(
            RuntimeError,
            "executor failed to generate valid structured output "
            "after 2 attempts",
        ):
            await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 2)
        self.assertListEqual(verifier.received, [])

    async def test_verifier_stops_after_structured_output_retries(
        self,
    ) -> None:
        """A verifier cannot retry a missing verdict indefinitely."""
        executor = StubAgent("executor", [[_report()]])
        verifier = StubAgent(
            "verifier",
            [[_no_output("verifier")]],
            max_calls=2,
        )
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        with self.assertRaisesRegex(
            RuntimeError,
            "verifier failed to generate valid structured output "
            "after 2 attempts",
        ):
            await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 1)
        self.assertEqual(len(verifier.received), 2)

    async def test_verifier_counts_a_missing_final_message_as_retry(
        self,
    ) -> None:
        """An empty verifier stream consumes the same retry budget."""
        executor = StubAgent("executor", [[_report()]])
        verifier = StubAgent("verifier", [[]], max_calls=2)
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        with self.assertRaisesRegex(
            RuntimeError,
            "verifier failed to generate valid structured output "
            "after 2 attempts",
        ):
            await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 1)
        self.assertEqual(len(verifier.received), 2)

    async def test_executor_retry_budget_survives_hitl_resume(self) -> None:
        """Parking does not reset the executor's consumed retry budget."""
        request = _confirm_request()
        executor = StubAgent(
            "executor",
            [
                [_no_output("executor")],
                [request],
                [_no_output("executor")],
            ],
            max_calls=3,
        )
        verifier = StubAgent("verifier", [[_verdict("pass")]])
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        await self._run(pipe, self.query)
        with self.assertRaisesRegex(
            RuntimeError,
            "executor failed to generate valid structured output "
            "after 2 attempts",
        ):
            await self._run(
                pipe,
                UserConfirmResultEvent(
                    reply_id="executor-reply",
                    confirm_results=[],
                ),
            )

        self.assertEqual(len(executor.received), 3)
        self.assertListEqual(verifier.received, [])

    async def test_verifier_retry_budget_survives_hitl_resume(self) -> None:
        """Parking does not reset the verifier's consumed retry budget."""
        request = _confirm_request("verifier-reply")
        executor = StubAgent("executor", [[_report()]])
        verifier = StubAgent(
            "verifier",
            [
                [_no_output("verifier")],
                [request],
                [_no_output("verifier")],
            ],
            max_calls=3,
        )
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        await self._run(pipe, self.query)
        with self.assertRaisesRegex(
            RuntimeError,
            "verifier failed to generate valid structured output "
            "after 2 attempts",
        ):
            await self._run(
                pipe,
                UserConfirmResultEvent(
                    reply_id="verifier-reply",
                    confirm_results=[],
                ),
            )

        self.assertEqual(len(executor.received), 1)
        self.assertEqual(len(verifier.received), 3)

    async def test_reprompts_a_verifier_that_skips_the_tool(self) -> None:
        """A final message with no verdict is not a refusal: the verifier
        is reminded rather than the executor being sent back."""
        executor = StubAgent("executor", [[_report()]])
        verifier = StubAgent(
            "verifier",
            [[_no_output("verifier")], [_verdict("pass")]],
        )
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        await self._run(pipe, self.query)

        self.assertEqual(len(verifier.received), 2)
        self.assertIn(
            "GenerateStructuredOutput",
            verifier.received[1].get_text_content(),
        )
        # A malfunction is not charged to the executor.
        self.assertEqual(len(executor.received), 1)

    async def test_reprompts_an_executor_that_skips_the_tool(self) -> None:
        """The same for the executor: a missing report is asked for again
        rather than read through as if it were there."""
        executor = StubAgent(
            "executor",
            [[_no_output("executor")], [_report()]],
        )
        verifier = StubAgent("verifier", [[_verdict("pass")]])
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        await self._run(pipe, self.query)

        self.assertEqual(len(executor.received), 2)
        self.assertIn(
            "GenerateStructuredOutput",
            executor.received[1].get_text_content(),
        )
        self.assertEqual(len(verifier.received), 1)

    async def test_a_parked_executor_is_not_verified(self) -> None:
        """The work is unfinished while the executor waits on a human, so
        there is nothing for the verifier to judge yet."""
        request = _confirm_request()
        executor = StubAgent("executor", [[request]])
        verifier = StubAgent("verifier", [[_verdict("pass")]])
        pipe = GoalPipeline(executor, verifier)

        yielded = await self._run(pipe, self.query)

        self.assertListEqual(yielded, [request])
        self.assertListEqual(verifier.received, [])

    async def test_resumes_into_the_agent_that_parked(self) -> None:
        """The reply id sends the answer back to whoever asked for it."""
        request = _confirm_request()
        executor = StubAgent("executor", [[request], [_report()]])
        verifier = StubAgent("verifier", [[_verdict("pass")]])
        pipe = GoalPipeline(executor, verifier)

        await self._run(pipe, self.query)

        answer = UserConfirmResultEvent(
            reply_id="executor-reply",
            confirm_results=[
                ConfirmResult(confirmed=True, tool_call=request.tool_calls[0]),
            ],
        )
        await self._run(pipe, answer)

        self.assertEqual(executor.received[1], answer)
        self.assertEqual(len(verifier.received), 1)

    async def test_resume_keeps_the_iteration_budget(self) -> None:
        """Resuming does not hand the run a fresh set of attempts.

        Round one is refused and round two parks. With a budget of two,
        the resumed round is the last one — were the budget to restart,
        the executor would be sent back a fourth time.
        """
        executor = StubAgent(
            "executor",
            [[_report()], [_confirm_request()], [_report()]],
        )
        verifier = StubAgent(
            "verifier",
            [[_verdict("fail", "不行")], [_verdict("fail", "还是不行")]],
        )
        pipe = GoalPipeline(executor, verifier, max_iters=2)

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
        executor = StubAgent("executor", [[_report()]])
        verifier = StubAgent("verifier", [[_verdict("pass")]])
        pipe = GoalPipeline(executor, verifier)

        with self.assertRaises(ValueError):
            await self._run(
                pipe,
                UserConfirmResultEvent(
                    reply_id="nobody",
                    confirm_results=[],
                ),
            )
