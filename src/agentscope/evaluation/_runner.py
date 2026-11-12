# -*- coding: utf-8 -*-
"""Sequential case execution and local JSONL evaluation reports."""
import asyncio
from pathlib import Path
from typing import AsyncIterator, Callable, Sequence

from ..event import AgentEvent
from ..message import Msg
from ..types import ErrorInfo
from ._collector import TrajectoryCollector
from ._evaluator import EvaluatorBase
from ._trajectory import EvaluationCase, EvaluationResult, Trajectory


class EvaluationRunner:
    """Compose caller-owned execution, side-channel collection and scoring.

    No Agent/model is created and no action is automatically approved.
    The execution callback owns state isolation and human-input resumes.
    """

    def __init__(
        self,
        run_case: Callable[[EvaluationCase], AsyncIterator[AgentEvent | Msg]],
        evaluators: Sequence[EvaluatorBase],
    ) -> None:
        """Accept an event-stream callback and ordered scorers.

        A callback can return agent.reply_stream(..., yield_final_msg=True).
        Several streams can be joined only to resume the same logical reply.
        """
        self._run_case = run_case
        self._evaluators = tuple(evaluators)
        self._collectors: list[TrajectoryCollector] = []
        self._running = False

    async def run(
        self,
        cases: Sequence[EvaluationCase],
    ) -> list[EvaluationResult]:
        """Run cases sequentially, preserving partial snapshots on failure.

        Duplicate case IDs and concurrent runs are rejected before resetting
        snapshots. A callback ending while awaiting input produces a waiting
        trajectory; the runner supplies no confirmation. Collection errors,
        evaluator errors and cancellation propagate instead of becoming scores.
        """
        if self._running:
            raise RuntimeError("This runner already has an active run.")
        fixtures = [case.model_copy(deep=True) for case in cases]
        if len({case.id for case in fixtures}) != len(fixtures):
            raise ValueError("Evaluation case IDs must be unique.")
        self._running = True
        self._collectors = []
        results = []
        try:
            for case in fixtures:
                collector = TrajectoryCollector(case)
                self._collectors.append(collector)
                try:
                    await self._collect(case, collector)
                except asyncio.CancelledError:
                    collector.end_segment("cancelled")
                    raise
                except Exception as error:
                    collector.end_segment(
                        "error",
                        ErrorInfo(
                            message=(
                                f"{type(error).__name__} while collecting "
                                "the case event stream."
                            ),
                        ),
                    )
                    raise
                collector.end_segment("exhausted")
                trajectory = collector.snapshot()
                metrics = []
                for evaluator in self._evaluators:
                    metrics.extend(
                        await evaluator.evaluate(
                            trajectory.model_copy(deep=True),
                            case.model_copy(deep=True),
                        ),
                    )
                results.append(
                    EvaluationResult(
                        case_id=case.id,
                        trajectory=trajectory,
                        metrics=metrics,
                    ),
                )
            return results
        finally:
            self._running = False

    async def _collect(
        self,
        case: EvaluationCase,
        collector: TrajectoryCollector,
    ) -> None:
        """Consume and close a stream without replacing its original error."""
        stream = self._run_case(case.model_copy(deep=True))
        primary_error: BaseException | None = None
        try:
            async for item in stream:
                collector.record(item)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                try:
                    await close()
                except BaseException:
                    if primary_error is None:
                        raise

    def snapshot(self) -> list[Trajectory]:
        """Copy all started cases, including the current or cancelled case."""
        return [collector.snapshot() for collector in self._collectors]


def write_jsonl(
    results: Sequence[EvaluationResult],
    path: str | Path,
    *,
    overwrite: bool = False,
) -> None:
    """Write one result per UTF-8 line, retaining nulls and diagnostics.

    The parent directory must exist. Existing files are protected unless
    overwrite=True. Serialize before opening the destination so serialization
    errors cannot truncate an existing report. Filesystem errors propagate.
    """
    lines = [result.model_dump_json() for result in results]
    with Path(path).open(
        "w" if overwrite else "x",
        encoding="utf-8",
        newline="\n",
    ) as report:
        for line in lines:
            report.write(line + "\n")
