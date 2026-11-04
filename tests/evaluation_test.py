# -*- coding: utf-8 -*-
"""Offline tests for side-channel trajectory collection and evaluation."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import AsyncIterator
from unittest import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.evaluation import (
    EvaluationCase,
    EvaluationResult,
    EvaluationRunner,
    Trajectory,
    TrajectoryCollector,
    TrajectoryEvaluator,
    write_jsonl,
)
from agentscope.event import (
    AgentEvent,
    CustomEvent,
    ModelCallEndEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import (
    AssistantMsg,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultState,
    Usage,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.types import ReplyFinishedReason


def _start() -> ReplyStartEvent:
    """Start one fixed reply."""
    return ReplyStartEvent(session_id="session", reply_id="reply", name="bot")


def _call(
    call_id: str = "call",
    name: str = "weather",
    arguments: str = '{"city": "London"}',
) -> list[AgentEvent]:
    """A complete streamed tool request."""
    return [
        ToolCallStartEvent(
            reply_id="reply",
            tool_call_id=call_id,
            tool_call_name=name,
        ),
        ToolCallDeltaEvent(
            reply_id="reply",
            tool_call_id=call_id,
            delta=arguments,
        ),
        ToolCallEndEvent(reply_id="reply", tool_call_id=call_id),
    ]


def _result(
    call_id: str = "call",
    state: ToolResultState = ToolResultState.SUCCESS,
    name: str = "weather",
) -> list[AgentEvent]:
    """A complete tool result."""
    return [
        ToolResultStartEvent(
            reply_id="reply",
            tool_call_id=call_id,
            tool_call_name=name,
        ),
        ToolResultTextDeltaEvent(
            reply_id="reply",
            tool_call_id=call_id,
            delta="sunny",
        ),
        ToolResultEndEvent(
            reply_id="reply",
            tool_call_id=call_id,
            state=state,
        ),
    ]


class EvaluationTest(IsolatedAsyncioTestCase):
    """Exercise collection, semantic evidence, cancellation and real Agents."""

    def setUp(self) -> None:
        """Build a labelled task with one tool schema."""
        self.case = EvaluationCase(
            id="weather",
            inputs=[UserMsg("user", "Weather in London?")],
            reference={"expected_tools": ["weather"], "final_text": "sunny"},
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                },
            ],
        )

    def _collect(self, events: list[AgentEvent | Msg]) -> Trajectory:
        """Observe a complete fixture stream."""
        collector = TrajectoryCollector(self.case)
        for item in events:
            collector.record(item)
        collector.end_segment("exhausted")
        return collector.snapshot()

    async def test_happy_path_and_usage_not_double_counted(self) -> None:
        """Keep final output separate from the rebuilt tool trajectory."""
        usage = ModelCallEndEvent(
            reply_id="reply",
            input_tokens=10,
            output_tokens=5,
        )
        trajectory = self._collect(
            [
                _start(),
                *_call(),
                *_result(),
                usage,
                usage,
                ReplyEndEvent(session_id="session", reply_id="reply"),
                AssistantMsg(
                    "bot",
                    "sunny",
                    id="reply",
                    usage=Usage(input_tokens=10, output_tokens=5),
                ),
            ],
        )
        self.assertEqual(trajectory.reported_usage.input_tokens, 10)
        self.assertFalse(trajectory.usage_verified)
        self.assertEqual(
            len(trajectory.reply.get_content_blocks("tool_call")),
            1,
        )
        self.assertEqual(trajectory.output.get_text_content(), "sunny")
        metrics = {
            item.name: item
            for item in await TrajectoryEvaluator().evaluate(
                trajectory,
                self.case,
            )
        }
        for name in (
            "tool_result_success_rate",
            "argument_validity_rate",
            "tool_selection_accuracy",
            "task_success",
        ):
            self.assertEqual(metrics[name].value, 1.0)
        self.assertEqual(metrics["redundant_tool_call_rate"].value, 0.0)

    async def test_successful_wrong_tool_is_not_correct(self) -> None:
        """A successful tool execution can still select the wrong tool."""
        trajectory = self._collect(
            [
                _start(),
                *_call(name="delete_file"),
                *_result(name="delete_file"),
            ],
        )
        metrics = {
            item.name: item
            for item in await TrajectoryEvaluator().evaluate(
                trajectory,
                self.case,
            )
        }
        self.assertEqual(metrics["tool_result_success_rate"].value, 1.0)
        self.assertEqual(metrics["tool_selection_accuracy"].value, 0.0)
        self.assertEqual(metrics["redundant_tool_call_rate"].value, 1.0)
        self.assertIsNone(metrics["argument_validity_rate"].value)

    async def test_unknown_evidence_and_empty_inputs(self) -> None:
        """Missing golden/schema/token data never produces a passing score."""
        case = EvaluationCase(id="empty", inputs=[], tool_schemas=[])
        trajectory = TrajectoryCollector(case).snapshot()
        metrics = await TrajectoryEvaluator().evaluate(trajectory, case)
        self.assertTrue(all(item.value is None for item in metrics))
        self.assertTrue(all(item.reason for item in metrics))
        self.assertIsNone(trajectory.reported_usage)

    async def test_argument_validation_and_missing_results(self) -> None:
        """Score invalid arguments only when their schema is known."""
        trajectory = self._collect(
            [
                _start(),
                *_call("bad-json", arguments="{"),
                *_call("bad-type", arguments='{"city": 3}'),
                *_call("long", arguments='{"city": "' + "x" * 100000 + '"}'),
            ],
        )
        metrics = {
            item.name: item
            for item in await TrajectoryEvaluator().evaluate(
                trajectory,
                self.case,
            )
        }
        self.assertAlmostEqual(metrics["argument_validity_rate"].value, 1 / 3)
        self.assertIsNone(metrics["tool_result_success_rate"].value)
        self.assertEqual(
            metrics["tool_result_success_rate"].details["missing_results"],
            3,
        )

    async def test_error_denial_interruption_and_running_results(self) -> None:
        """Terminal states are counted transparently; running is excluded."""
        events = [_start()]
        for index, state in enumerate(ToolResultState):
            call_id = str(index)
            events.extend(_call(call_id))
            events.extend(_result(call_id, state))
        metrics = {
            item.name: item
            for item in await TrajectoryEvaluator().evaluate(
                self._collect(events),
                self.case,
            )
        }
        score = metrics["tool_result_success_rate"]
        self.assertEqual(score.value, 0.25)
        self.assertEqual(score.sample_count, 4)
        self.assertEqual(score.details["running_results"], 1)

    async def test_human_and_external_waiting_resume_same_reply(self) -> None:
        """Keep waiting on stream exhaustion and resume without a new start."""
        for event_class in (
            RequireUserConfirmEvent,
            RequireExternalExecutionEvent,
        ):
            with self.subTest(event_class=event_class):
                collector = TrajectoryCollector(self.case)
                for item in [_start(), *_call()]:
                    collector.record(item)
                collector.record(
                    event_class(
                        reply_id="reply",
                        tool_calls=[
                            ToolCallBlock(
                                id="call",
                                name="weather",
                                input='{"city":"London"}',
                            ),
                        ],
                    ),
                )
                collector.end_segment("exhausted")
                self.assertTrue(collector.snapshot().awaiting_input)
                self.assertIsNone(collector.snapshot().finished_reason)
                for item in _result():
                    collector.record(item)
                self.assertFalse(collector.snapshot().awaiting_input)
                collector.record(
                    ReplyEndEvent(
                        session_id="session",
                        reply_id="reply",
                    ),
                )
                collector.end_segment("exhausted")
                self.assertEqual(
                    collector.snapshot().finished_reason,
                    ReplyFinishedReason.COMPLETED,
                )

    async def test_final_message_clears_waiting_but_placeholder_does_not(
        self,
    ) -> None:
        """Require an explicit termination before clearing a parked reply."""
        for reason in ReplyFinishedReason:
            with self.subTest(reason=reason):
                collector = TrajectoryCollector(self.case)
                for item in [_start(), *_call()]:
                    collector.record(item)
                collector.record(
                    RequireUserConfirmEvent(
                        reply_id="reply",
                        tool_calls=[
                            ToolCallBlock(
                                id="call",
                                name="weather",
                                input='{"city":"London"}',
                            ),
                        ],
                    ),
                )
                collector.record(
                    AssistantMsg(
                        "bot",
                        "Awaiting confirmation.",
                        id="reply",
                    ),
                )
                collector.end_segment("exhausted")
                self.assertTrue(collector.snapshot().awaiting_input)
                self.assertIsNone(collector.snapshot().finished_reason)

                collector.record(
                    AssistantMsg(
                        "bot",
                        "Finished.",
                        id="reply",
                        finished_reason=reason,
                    ),
                )
                snapshot = collector.snapshot()
                self.assertEqual(snapshot.finished_reason, reason)
                self.assertFalse(snapshot.awaiting_input)

    async def test_copy_isolation_orphans_custom_and_wrong_reply(self) -> None:
        """Diagnostics preserve gaps without mutating caller-owned objects."""
        collector = TrajectoryCollector(self.case)
        collector.record(CustomEvent(name="progress", value={"step": 1}))
        collector.record(_start())
        collector.record(
            ToolCallDeltaEvent(
                reply_id="reply",
                tool_call_id="missing",
                delta="{}",
            ),
        )
        original = collector.snapshot()
        self.assertTrue(original.diagnostics)
        original.events.clear()
        original.inputs[0].content.clear()
        self.assertEqual(len(collector.snapshot().events), 3)
        self.assertTrue(self.case.inputs[0].content)
        with self.assertRaises(ValueError):
            collector.record(
                ReplyStartEvent(
                    session_id="session",
                    reply_id="other",
                    name="bot",
                ),
            )
        with self.assertRaises(ValueError):
            collector.record(
                ReplyStartEvent(
                    session_id="other",
                    reply_id="reply",
                    name="bot",
                ),
            )

    async def test_termination_does_not_reopen_or_count_late_usage(
        self,
    ) -> None:
        """Keep late activity inspectable without corrupting terminal state."""
        end = ReplyEndEvent(session_id="session", reply_id="reply")
        trajectory = self._collect(
            [
                _start(),
                end,
                end,
                ModelCallEndEvent(
                    reply_id="reply",
                    input_tokens=9,
                    output_tokens=2,
                ),
            ],
        )
        self.assertEqual(trajectory.finished_reason, "completed")
        self.assertIsNone(trajectory.reported_usage)
        self.assertEqual(len(trajectory.events), 3)
        self.assertTrue(trajectory.diagnostics)

    async def test_duration_and_bad_timestamps(self) -> None:
        """Include waiting time and leave inconsistent timestamps unknown."""
        start = _start().model_copy(
            update={"created_at": "2026-01-01T00:00:00"},
        )
        end = ReplyEndEvent(
            session_id="session",
            reply_id="reply",
            created_at="2026-01-01T00:00:10",
        )
        self.assertEqual(self._collect([start, end]).duration_ms, 10000)
        end.created_at = "2025-01-01T00:00:00"
        self.assertIsNone(self._collect([start, end]).duration_ms)

    async def test_schema_references_stay_offline(self) -> None:
        """Resolve local definitions and reject remote schema retrieval."""
        trajectory = self._collect([_start(), *_call()])
        parameters: dict = {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        }
        self.case.tool_schemas = [
            {"name": "weather", "parameters": parameters},
        ]
        parameters["$defs"] = {"city": {"type": "string"}}
        parameters["properties"]["city"] = {"$ref": "#/$defs/city"}
        metrics = await TrajectoryEvaluator().evaluate(trajectory, self.case)
        self.assertEqual(
            next(
                item.value
                for item in metrics
                if item.name == "argument_validity_rate"
            ),
            1.0,
        )
        parameters["properties"]["city"] = {
            "$ref": "https://example.invalid/schema.json",
        }
        with self.assertRaisesRegex(ValueError, "local schema references"):
            await TrajectoryEvaluator().evaluate(trajectory, self.case)

    async def test_retries_need_explicit_golden_multiplicity(self) -> None:
        """Repeated names alone do not establish redundant work."""
        trajectory = self._collect(
            [
                _start(),
                *_call("first"),
                *_result("first"),
                *_call("retry"),
                *_result("retry"),
            ],
        )
        self.case.reference = {"expected_tools": ["weather", "weather"]}
        metrics = {
            item.name: item
            for item in await TrajectoryEvaluator().evaluate(
                trajectory,
                self.case,
            )
        }
        self.assertEqual(metrics["redundant_tool_call_rate"].value, 0.0)
        self.case.reference = None
        metrics = {
            item.name: item
            for item in await TrajectoryEvaluator().evaluate(
                trajectory,
                self.case,
            )
        }
        self.assertEqual(metrics["tool_result_success_rate"].value, 1.0)
        self.assertIsNone(metrics["tool_selection_accuracy"].value)
        self.assertIsNone(metrics["redundant_tool_call_rate"].value)

    async def test_predicate_can_decline_and_cannot_mutate_fixture(
        self,
    ) -> None:
        """Give task-specific oracles isolated copies and allow no verdict."""

        async def oracle(
            trajectory: Trajectory,
            case: EvaluationCase,
        ) -> bool | None:
            trajectory.events.clear()
            case.inputs.clear()
            return None

        trajectory = self._collect([_start()])
        metrics = await TrajectoryEvaluator(oracle).evaluate(
            trajectory,
            self.case,
        )
        self.assertIsNone(
            next(
                item.value for item in metrics if item.name == "task_success"
            ),
        )
        self.assertTrue(trajectory.events)
        self.assertTrue(self.case.inputs)

    async def test_runner_cancel_and_concurrent_rejection(self) -> None:
        """Keep the in-flight snapshot while propagating cancellation."""
        started = asyncio.Event()
        closed = asyncio.Event()

        async def execute(
            case: EvaluationCase,
        ) -> AsyncIterator[AgentEvent | Msg]:
            del case
            try:
                yield _start()
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

        runner = EvaluationRunner(execute, [TrajectoryEvaluator()])
        task = asyncio.create_task(runner.run([self.case]))
        await started.wait()
        with self.assertRaises(RuntimeError):
            await runner.run([self.case])
        self.assertEqual(len(runner.snapshot()[0].events), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        trajectory = runner.snapshot()[0]
        self.assertEqual(trajectory.collection_status, "cancelled")
        self.assertIsNone(trajectory.finished_reason)
        trajectory.events.clear()
        self.assertEqual(len(runner.snapshot()[0].events), 1)

    async def test_runner_errors_duplicates_and_reset(self) -> None:
        """Validate cases first and retain a sanitized error record."""

        async def execute(
            case: EvaluationCase,
        ) -> AsyncIterator[AgentEvent | Msg]:
            del case
            yield _start()
            raise RuntimeError("secret-value")

        runner = EvaluationRunner(execute, [])
        with self.assertRaises(ValueError):
            await runner.run([self.case, self.case])
        self.assertEqual(runner.snapshot(), [])
        with self.assertRaisesRegex(RuntimeError, "secret-value"):
            await runner.run([self.case])
        trajectory = runner.snapshot()[0]
        self.assertEqual(trajectory.collection_status, "error")
        self.assertNotIn("secret-value", trajectory.collection_error.message)
        self.assertEqual(await runner.run([]), [])
        self.assertEqual(runner.snapshot(), [])

    async def test_real_agent_original_api_compatible_and_jsonl(self) -> None:
        """Adapt a real Agent with a fake model without changing its API."""

        def make_agent() -> Agent:
            model = MockModel(context_size=4096)
            model.set_responses(
                [
                    ChatResponse(
                        content=[TextBlock(text="sunny")],
                        is_last=True,
                    ),
                ],
            )
            return Agent(
                name="bot",
                system_prompt="Answer briefly.",
                model=model,
                injection_config=InjectionConfig(inject_runtime_state=False),
            )

        baseline = await make_agent().reply(self.case.inputs)

        def execute(case: EvaluationCase) -> AsyncIterator[AgentEvent | Msg]:
            return make_agent().reply_stream(case.inputs, yield_final_msg=True)

        runner = EvaluationRunner(execute, [TrajectoryEvaluator()])
        results = await runner.run([self.case])
        self.assertEqual(
            baseline.get_text_content(),
            results[0].trajectory.output.get_text_content(),
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.jsonl"
            write_jsonl(results, path)
            parsed = EvaluationResult.model_validate_json(
                path.read_text(encoding="utf-8").strip(),
            )
            self.assertEqual(parsed.case_id, self.case.id)
            self.assertFalse(parsed.trajectory.usage_verified)
            with self.assertRaises(FileExistsError):
                write_jsonl(results, path)
            write_jsonl(results, path, overwrite=True)
