# -*- coding: utf-8 -*-
"""Evidence-based scoring of observed Agent trajectories."""
from abc import ABC, abstractmethod
from collections import Counter
import inspect
import json
from typing import Awaitable, Callable

from jsonschema import validators

from ..message import ToolCallBlock, ToolResultBlock, ToolResultState
from ..types import ReplyFinishedReason
from ._trajectory import EvaluationCase, MetricResult, Trajectory


class EvaluatorBase(ABC):
    """Score observations without rerunning tools or mutating a trajectory.

    Semantic correctness requires reference data or a task-specific predicate.
    Running or missing results must not count as terminal successes/failures.
    """

    @abstractmethod
    async def evaluate(
        self,
        trajectory: Trajectory,
        case: EvaluationCase,
    ) -> list[MetricResult]:
        """Return metrics, using None with a reason for missing evidence."""
        raise NotImplementedError


def _rate(
    name: str,
    numerator: int,
    denominator: int,
    **details: object,
) -> MetricResult:
    """Keep denominators and unavailable empty samples explicit."""
    return MetricResult(
        name=name,
        value=numerator / denominator if denominator else None,
        sample_count=denominator,
        reason=None if denominator else "No eligible observations.",
        details={
            "numerator": numerator,
            "denominator": denominator,
            **details,
        },
    )


class TrajectoryEvaluator(EvaluatorBase):
    """Compute deterministic metrics from observable evidence.

    reference["expected_tools"] is an exhaustive, order-independent list of
    expected tool names, including multiplicities. It supplies the golden
    specification for selection accuracy and surplus-call rate; it must list
    acceptable retries/pagination when they are part of the expected solution.
    These metrics are unavailable without that specification.

    Task success uses an optional predicate, or exact comparison against
    reference["final_text"] on a completed reply's explicit final Msg.
    No LLM judge or network access is involved. Schemas check argument syntax,
    not semantic appropriateness. References within schemas must be local
    fragments; remote schemas are not fetched. Reported tokens are not billing.

    Args:
        task_success:
            A sync or async task-specific predicate returning bool, or None
            when available observations cannot establish success.
    """

    def __init__(
        self,
        task_success: Callable[
            [Trajectory, EvaluationCase],
            bool | None | Awaitable[bool | None],
        ]
        | None = None,
    ) -> None:
        """Optionally supply the task-success oracle."""
        self._task_success = task_success

    async def evaluate(
        self,
        trajectory: Trajectory,
        case: EvaluationCase,
    ) -> list[MetricResult]:
        """Score a matching fixture without changing its inputs."""
        if trajectory.case_id != case.id:
            raise ValueError("The trajectory and case identifiers must match.")
        blocks = trajectory.reply.content if trajectory.reply else []
        calls = [block for block in blocks if isinstance(block, ToolCallBlock)]
        completed = [call for call in calls if call.finished_at is not None]
        results = [
            block for block in blocks if isinstance(block, ToolResultBlock)
        ]
        terminal = [
            result
            for result in results
            if result.state != ToolResultState.RUNNING
        ]
        # Final tool states include validation errors and permission denials;
        # events cannot reliably isolate actual I/O execution attempts.
        metrics = [
            _rate(
                "tool_result_success_rate",
                sum(
                    result.state == ToolResultState.SUCCESS
                    for result in terminal
                ),
                len(terminal),
                running_results=len(results) - len(terminal),
                missing_results=len(
                    {call.id for call in calls}
                    - {result.id for result in results},
                ),
                terminal_states=dict(
                    Counter(str(result.state) for result in terminal),
                ),
            ),
            self._argument_validity(completed, case),
            *self._selection(completed, case),
        ]
        metrics.append(await self._success(trajectory, case))
        metrics.append(
            MetricResult(
                name="observed_duration_ms",
                value=trajectory.duration_ms,
                sample_count=int(trajectory.duration_ms is not None),
                reason=None
                if trajectory.duration_ms is not None
                else "Insufficient or inconsistent event timestamps.",
            ),
        )
        usage = trajectory.reported_usage
        for name in ("input_tokens", "output_tokens"):
            metrics.append(
                MetricResult(
                    name="reported_" + name,
                    value=getattr(usage, name) if usage is not None else None,
                    sample_count=int(usage is not None),
                    reason=None if usage is not None else "No reported usage.",
                    details={"usage_verified": False},
                ),
            )
        return metrics

    @staticmethod
    def _argument_validity(
        calls: list[ToolCallBlock],
        case: EvaluationCase,
    ) -> MetricResult:
        """Validate only calls with supplied, valid JSON Schemas."""
        if case.tool_schemas is None:
            return MetricResult(
                name="argument_validity_rate",
                reason="No tool schemas supplied.",
            )
        schemas = {}
        for schema in case.tool_schemas:
            function = schema.get("function", schema)
            if not isinstance(function, dict):
                raise ValueError(
                    "A tool schema must contain a function object.",
                )
            name = function.get("name")
            if not isinstance(name, str) or name in schemas:
                raise ValueError("Tool schema names must be unique strings.")
            parameters = function.get("parameters")
            if parameters is None:
                raise ValueError("A tool schema must supply parameters.")
            _check_local_references(parameters)
            validator_class = validators.validator_for(parameters)
            validator_class.check_schema(parameters)
            schemas[name] = validator_class(parameters)
        valid = 0
        eligible = 0
        unknown = []
        for call in calls:
            if call.name not in schemas:
                unknown.append(call.id)
                continue
            eligible += 1
            try:
                arguments = json.loads(call.input)
            except (ValueError, TypeError):
                continue
            valid += int(schemas[call.name].is_valid(arguments))
        return _rate(
            "argument_validity_rate",
            valid,
            eligible,
            excluded_unknown_schema=unknown,
        )

    @staticmethod
    def _selection(
        calls: list[ToolCallBlock],
        case: EvaluationCase,
    ) -> list[MetricResult]:
        """Compare observed tool-name counts with explicit golden data."""
        reference = case.reference or {}
        names = ("tool_selection_accuracy", "redundant_tool_call_rate")
        if "expected_tools" not in reference:
            return [
                MetricResult(name=name, reason="No expected_tools reference.")
                for name in names
            ]
        expected = reference["expected_tools"]
        if not isinstance(expected, list) or not all(
            isinstance(name, str) for name in expected
        ):
            raise ValueError("expected_tools must be a list of tool names.")
        observed = Counter(call.name for call in calls)
        matched = sum((observed & Counter(expected)).values())
        return [
            _rate(
                names[0],
                matched,
                len(calls),
                expected_count=len(expected),
                unmatched_expected=len(expected) - matched,
            ),
            _rate(
                names[1],
                len(calls) - matched,
                len(calls),
                reference_is_exhaustive=True,
            ),
        ]

    async def _success(
        self,
        trajectory: Trajectory,
        case: EvaluationCase,
    ) -> MetricResult:
        """Use an oracle rather than absence of exceptions as task success."""
        success = None
        reason = "No task-success predicate or final_text reference."
        if self._task_success is not None:
            result = self._task_success(
                trajectory.model_copy(deep=True),
                case.model_copy(deep=True),
            )
            success = await result if inspect.isawaitable(result) else result
            if success is not None and not isinstance(success, bool):
                raise TypeError("Task-success predicates return bool or None.")
            reason = "Task-success predicate returned no verdict."
        elif case.reference is not None and "final_text" in case.reference:
            expected = case.reference["final_text"]
            if not isinstance(expected, str):
                raise ValueError("final_text must be a string.")
            if trajectory.finished_reason is None:
                reason = "The logical reply has not terminated."
            elif trajectory.finished_reason != ReplyFinishedReason.COMPLETED:
                success = False
            elif trajectory.output is None:
                reason = "No explicit final message was collected."
            else:
                success = trajectory.output.get_text_content() == expected
        return MetricResult(
            name="task_success",
            value=float(success) if success is not None else None,
            sample_count=int(success is not None),
            reason=reason if success is None else None,
        )


def _check_local_references(schema: object) -> None:
    """Keep argument validation offline, even for caller-supplied schemas."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in ("$ref", "$dynamicRef", "$recursiveRef") and (
                not isinstance(value, str) or not value.startswith("#")
            ):
                raise ValueError("Only local schema references are supported.")
            _check_local_references(value)
    elif isinstance(schema, list):
        for item in schema:
            _check_local_references(item)
