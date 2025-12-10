# -*- coding: utf-8 -*-
"""The evaluator storage base class for storing solution and evaluation
results."""
from abc import abstractmethod
from typing import Any, Callable

from .._metric_base import MetricResult
from .._solution import SolutionOutput
from ...agent import AgentBase
from ...types import JSONSerializableObject


class EvaluatorStorageBase:
    """Used to store the solution results and evaluation results to support
    resuming the evaluation process"""

    @abstractmethod
    def save_solution_result(
        self,
        task_id: str,
        repeat_id: str,
        output: SolutionOutput,
        **kwargs: Any,
    ) -> None:
        """Save the solution result.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.
            output (`SolutionOutput`):
                The solution output to be saved.
        """

    @abstractmethod
    def get_evaluation_result(
        self,
        task_id: str,
        repeat_id: str,
        metric_name: str,
    ) -> MetricResult:
        """Get the evaluation result by the given task id and repeat id

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.
            metric_name (`str`):
                The metric name.

        Returns:
            `MetricResult`:
                The evaluation result for the given task and repeat ID.
        """

    @abstractmethod
    def save_evaluation_result(
        self,
        task_id: str,
        repeat_id: str,
        evaluation: MetricResult,
        **kwargs: Any,
    ) -> None:
        """Save the evaluation result.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.
            evaluation (`MetricResult`):
                The evaluation result to be saved.
        """

    @abstractmethod
    def get_solution_result(
        self,
        task_id: str,
        repeat_id: str,
        **kwargs: Any,
    ) -> SolutionOutput:
        """Get the solution result for the given task and repeat id.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.

        Returns:
            `SolutionOutput`:
                The solution output for the given task and repeat ID.
        """

    @abstractmethod
    def solution_result_exists(self, task_id: str, repeat_id: str) -> bool:
        """Check if the solution for the given task and repeat is finished.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.

        Returns:
            `bool`:
                True if the solution result file exists, False otherwise.
        """

    @abstractmethod
    def evaluation_result_exists(
        self,
        task_id: str,
        repeat_id: str,
        metric_name: str,
    ) -> bool:
        """Check if the evaluation result for the given solution and metric
        is finished.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.
            metric_name (`str`):
                The name of the metric.

        Returns:
            `bool`:
                True if the evaluation result file exists, False otherwise.
        """

    @abstractmethod
    def save_aggregation_result(
        self,
        aggregation_result: dict,
        **kwargs: Any,
    ) -> None:
        """Save the aggregation result.

        Args:
            aggregation_result (`dict`):
                A dictionary containing the aggregation result.
        """

    @abstractmethod
    def aggregation_result_exists(
        self,
        **kwargs: Any,
    ) -> bool:
        """Check if the aggregation result exists

        Returns:
            `bool`:
                `True` if the aggregation result file exists.
        """

    @abstractmethod
    def save_evaluation_meta(self, meta_info: dict) -> None:
        """Save the evaluation meta information.

        Args:
            meta_info (`dict`):
                A dictionary containing the meta information.
        """

    @abstractmethod
    def save_task_meta(
        self,
        task_id: str,
        meta_info: dict[str, JSONSerializableObject],
    ) -> None:
        """Save the task meta information.

        Args:
            task_id (`str`):
                The task ID.
            meta_info (`dict[str, JSONSerializableObject]`):
                The task meta information to be saved, which should be JSON
                serializable.
        """

    @abstractmethod
    def save_solution_stats(
        self,
        task_id: str,
        repeat_id: str,
        stats: dict,
    ) -> None:
        """Save the solution statistics information for a given task and
        repeat ID.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.
            stats (`dict`):
                A dictionary containing the solution statistics to be saved.
        """

    @abstractmethod
    def get_solution_stats(
        self,
        task_id: str,
        repeat_id: str,
    ) -> dict:
        """Get the solution statistics information for a given task and
        repeat ID.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.

        Returns:
            `dict`:
                A dictionary containing the solution statistics for the given
                task and repeat ID.
        """

    @abstractmethod
    def get_agent_pre_print_hook(
        self,
        task_id: str,
        repeat_id: str,
    ) -> Callable[[AgentBase, dict], None]:
        """Get a pre-print hook function for the agent to save the agent
        printing in the evaluation storage.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.

        Returns:
            `Callable[[AgentBase, dict], None]`:
                A hook function that takes an `AgentBase` instance and a
                keyword arguments dictionary as input, saving the agent's
                printing Msg into the evaluation storage.
        """
