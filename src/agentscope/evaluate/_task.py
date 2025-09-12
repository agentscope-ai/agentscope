# -*- coding: utf-8 -*-
"""The base class for task in evaluation."""
from dataclasses import dataclass, field
from typing import Any

from ._solution import SolutionOutput
from ._metric_base import MetricBase, MetricResult
from ..types._json import JSONSerializableObject


@dataclass
class Task:
    """The base class for task in evaluation."""

    id: str
    """The unique identifier for the task."""

    input: JSONSerializableObject
    """The task input, which should be a JSON serializable object."""

    ground_truth: JSONSerializableObject
    """The task ground truth if exists, which should be a JSON serializable
    object."""

    metrics: list[MetricBase]
    """The metrics to evaluate the task, which should be a list of
    `MetricBase` objects."""

    tags: dict[str, str] | None = field(default_factory=lambda: None)
    """Tags to categorize the task, e.g. `{"difficulty": "easy",
    "cate": "math"}`."""

    metadata: dict[str, Any] | None = field(
        default_factory=lambda: None,
    )
    """Additional metadata for the task."""

    async def evaluate(self, solution: SolutionOutput) -> list[MetricResult]:
        """Evaluate the task with the given solution.

        Args:
            solution (`SolutionOutput`):
                The solution to evaluate the task with.

        Returns:
            `MetricResult`:
                The result of the evaluation.
        """
        evaluations = []
        for metric in self.metrics:
            result = await metric(solution)
            evaluations.append(result)
        return evaluations
