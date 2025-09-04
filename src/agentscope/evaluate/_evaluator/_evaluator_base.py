# -*- coding: utf-8 -*-
"""The base class for evaluator in evaluation."""
import collections
import json
from abc import abstractmethod
from typing import Callable, Coroutine, Any

from .._solution import SolutionOutput
from .._task import Task
from .._benchmark_base import BenchmarkBase
from .._evaluator_storage import EvaluatorStorageBase
from .._metric_base import MetricType
from ..._utils._common import _get_timestamp


class EvaluatorBase:
    """The class that runs the evaluation process."""

    def __init__(
        self,
        name: str,
        benchmark: BenchmarkBase,
        n_repeat: int,
        storage: EvaluatorStorageBase,
    ) -> None:
        """Initialize the evaluator.

        Args:
            name (`str`):
                The name of this evaluator.
            benchmark: (`BenchmarkBase`):
                A benchmark instance inheriting from `BenchmarkBase` that
                defines the evaluation dataset.
            n_repeat (`int`):
                How many times to repeat the evaluation for each task.
            storage (`EvaluatorStorageBase`):
                A instance inheriting from the child class of
                `EvaluatorStorageBase` that supports storing and loading
                solution output and evaluation results.
        """
        self.name = name
        self.benchmark = benchmark
        self.n_repeat = n_repeat
        self.storage = storage

    @abstractmethod
    async def run(
        self,
        solution: Callable[
            [Task, Callable],
            Coroutine[Any, Any, SolutionOutput],
        ],
    ) -> None:
        """Run the evaluation and return the results.

        Args:
            solution (`Callable[[Task, Callable], Coroutine[Any, Any, \
            SolutionOutput]]`):
                A async function that takes a `Task` instance and a pre-hook
                as input and returns a `SolutionOutput` instance.
        """

    async def _save_evaluation_meta(self) -> None:
        """Save the evaluation meta information."""
        self.storage.save_evaluation_meta(
            {
                "evaluation_name": self.name,
                "created_at": _get_timestamp(),
                "total_repeats": self.n_repeat,
                "benchmark": {
                    "name": self.benchmark.name,
                    "description": self.benchmark.description,
                    "total_tasks": len(self.benchmark),
                },
                "schema_version": 1,
            },
        )

    async def aggregate(self) -> None:  # pylint: disable=too-many-branches
        """Aggregate the evaluation results and save an overall result."""
        meta_info: dict = {
            "total_tasks": len(self.benchmark),
            "total_repeats": self.n_repeat,
            "repeats": {},
            "schema_version": 1,
        }

        for repeat_index in range(self.n_repeat):
            repeat_id = str(repeat_index)
            current_repeat: dict = {
                "completed_tasks": 0,
                "incomplete_tasks": 0,
                "metrics": {},
                "completed_ids": [],
                "incomplete_ids": [],
            }
            for task in self.benchmark:
                for metric in task.metrics:
                    # Create a new dict in aggregated_result
                    if metric.name not in current_repeat["metrics"]:
                        current_repeat["metrics"][metric.name] = {
                            "type": metric.metric_type,
                            "involved_tasks": 0,
                            "completed_tasks": 0,
                            "incomplete_tasks": 0,
                            "aggregation": {},
                            "distribution": collections.defaultdict(list),
                        }

                    # Record the submitted task
                    current_repeat["metrics"][metric.name][
                        "involved_tasks"
                    ] += 1

                    # Not finished
                    if not self.storage.evaluation_result_exists(
                        task.id,
                        repeat_id,
                        metric.name,
                    ):
                        if task.id not in current_repeat["incomplete_ids"]:
                            current_repeat["incomplete_tasks"] += 1
                            current_repeat["incomplete_ids"].append(task.id)
                        current_repeat["metrics"][metric.name][
                            "incomplete_tasks"
                        ] += 1
                        continue

                    if task.id not in current_repeat["completed_ids"]:
                        current_repeat["completed_tasks"] += 1
                        current_repeat["completed_ids"].append(task.id)
                    current_repeat["metrics"][metric.name][
                        "completed_tasks"
                    ] += 1

                    # Get the evaluation result
                    eval_result = self.storage.get_evaluation_result(
                        task.id,
                        repeat_id,
                        metric.name,
                    )

                    # Record the metric result
                    if metric.metric_type == MetricType.CATEGORY:
                        current_repeat["metrics"][metric.name]["distribution"][
                            eval_result.result
                        ].append(
                            task.id,
                        )

                    elif metric.metric_type == MetricType.NUMERICAL:
                        current_repeat["metrics"][metric.name]["distribution"][
                            task.id
                        ] = eval_result.result

            print("Repeat ID:", repeat_id)

            for metric, value in current_repeat["metrics"].items():
                print("\tMetric:", metric)
                print("\t\tType:", value["type"])
                print("\t\tInvolved tasks:", value["involved_tasks"])
                print("\t\tCompleted tasks:", value["completed_tasks"])
                print("\t\tIncomplete tasks:", value["incomplete_tasks"])

                if value["type"] == MetricType.CATEGORY:
                    # Count the distribution
                    for category, task_ids in value["distribution"].items():
                        value["aggregation"][category] = (
                            len(task_ids) * 1.0 / value["involved_tasks"]
                        )

                elif value["type"] == MetricType.NUMERICAL:
                    scores = list(value["distribution"].values())
                    value["aggregation"] = {
                        "mean": sum(scores) / value["involved_tasks"],
                        "max": max(scores),
                        "min": min(scores),
                    }

                print(
                    "\t\tAggregation:",
                    json.dumps(
                        value["aggregation"],
                        indent=4,
                        ensure_ascii=False,
                    ).replace("\n", "\n\t\t"),
                )

            meta_info["repeats"][repeat_id] = current_repeat

        # save
        self.storage.save_aggregation_result(meta_info)
