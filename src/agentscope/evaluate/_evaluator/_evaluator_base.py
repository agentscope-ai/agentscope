# -*- coding: utf-8 -*-
"""The base class for evaluator in evaluation."""
import collections
import json
from abc import abstractmethod
from dataclasses import asdict
from typing import Callable, Coroutine, Any
from collections import defaultdict

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

    async def _save_task_meta(self, task: Task) -> None:
        """Save the task meta information.

        Args:
            task (`Task`):
                The task instance.
        """
        meta_info = asdict(task)
        meta_info.pop("metadata")
        self.storage.save_task_meta(
            task.id,
            meta_info,
        )

    # pylint: disable=too-many-branches, too-many-statements
    async def aggregate(self) -> None:
        """Aggregate the evaluation results and save an overall result."""
        meta_info: dict = {
            "total_tasks": len(self.benchmark),
            "total_repeats": self.n_repeat,
            "total_stats": {
                "llm": defaultdict(int),
                "agent": 0,
                "tool": defaultdict(int),
                "embedding": defaultdict(int),
                "chat_usage": {},
            },
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
                "stats": {
                    "llm": defaultdict(int),
                    "agent": 0,
                    "tool": defaultdict(int),
                    "embedding": defaultdict(int),
                    "chat_usage": {},
                },
            }
            for task in self.benchmark:
                current_stats = self.storage.get_solution_stats(
                    task.id,
                    repeat_id,
                )

                # llm
                for model_name, cnt in current_stats.get("llm", {}).items():
                    current_repeat["stats"]["llm"][model_name] += cnt

                # agent
                current_repeat["stats"]["agent"] += current_stats.get(
                    "agent",
                    0,
                )

                # tool
                for tool_name, cnt in current_stats.get("tool", {}).items():
                    current_repeat["stats"]["tool"][tool_name] += cnt

                # embedding
                for embedding_model, cnt in current_stats.get(
                    "embedding",
                    {},
                ).items():
                    current_repeat["stats"]["embedding"][
                        embedding_model
                    ] += cnt

                # chat usage
                for model_name, usage in current_stats.get(
                    "chat_usage",
                    {},
                ).items():
                    if model_name not in current_repeat["stats"]["chat_usage"]:
                        current_repeat["stats"]["chat_usage"][
                            model_name
                        ] = defaultdict(int)
                    current_repeat["stats"]["chat_usage"][model_name][
                        "input_tokens"
                    ] += usage.get("input_tokens", 0)
                    current_repeat["stats"]["chat_usage"][model_name][
                        "output_tokens"
                    ] += usage.get("output_tokens", 0)

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

            # Aggregate total stats
            repeat_stats = current_repeat["stats"]

            # llm
            for model_name, cnt in repeat_stats.get("llm", {}).items():
                meta_info["total_stats"]["llm"][model_name] += cnt

            # agent
            meta_info["total_stats"]["agent"] += repeat_stats.get("agent", 0)

            # tool
            for tool_name, cnt in repeat_stats.get("tool", {}).items():
                meta_info["total_stats"]["tool"][tool_name] += cnt

            # embedding
            for embedding_model, cnt in repeat_stats.get(
                "embedding",
                {},
            ).items():
                meta_info["total_stats"]["embedding"][embedding_model] += cnt

            # chat usage
            for model_name, usage in repeat_stats.get(
                "chat_usage",
                {},
            ).items():
                if model_name not in meta_info["total_stats"]["chat_usage"]:
                    meta_info["total_stats"]["chat_usage"][
                        model_name
                    ] = defaultdict(int)
                meta_info["total_stats"]["chat_usage"][model_name][
                    "input_tokens"
                ] += usage.get("input_tokens", 0)
                meta_info["total_stats"]["chat_usage"][model_name][
                    "output_tokens"
                ] += usage.get("output_tokens", 0)

        # save
        self.storage.save_aggregation_result(meta_info)
