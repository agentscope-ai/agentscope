# -*- coding: utf-8 -*-
"""A file system based evaluator storage."""
import json
import os
from json import JSONDecodeError
from typing import Any, Callable

from ._evaluator_storage_base import EvaluatorStorageBase
from .._solution import SolutionOutput
from .._metric_base import MetricResult
from ...agent import AgentBase
from ...message import Msg
from ...types import JSONSerializableObject


class FileEvaluatorStorage(EvaluatorStorageBase):
    """File system based evaluator storage, providing methods to save and
    retrieve evaluation results. So that the evaluation process can be resumed
    from the last saved state.

    The files are organized in a directory structure:
    - save_dir/
        - evaluation_result.json
        - evaluation_meta.json
        - {repeat_id}/
            - {task_id}/
                - solution.json
                - evaluation/
                    - {metric_name}.json
    """

    SOLUTION_FILE_NAME = "solution.json"
    SOLUTION_STATS_FILE_NAME = "stats.json"
    EVALUATION_DIR_NAME = "evaluation"
    EVALUATION_RESULT_FILE = "evaluation_result.json"
    EVALUATION_META_FILE = "evaluation_meta.json"
    TASK_META_FILE = "task_meta.json"
    AGENT_PRINTING_LOG = "logging.txt"

    def __init__(self, save_dir: str) -> None:
        """Initialize the file evaluator storage."""
        self.save_dir = os.path.abspath(save_dir)

    def _get_save_path(
        self,
        task_id: str,
        repeat_id: str | None,
        *args: str,
    ) -> str:
        """Get the save path for a given task, repeat ID, and additional path
        components.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str | None`):
                The repeat ID for the task, usually the index of the repeat
                evaluation. If None, it will be ignored in the path.
            *args (`str`):
                Additional path components to be appended.
        """
        path_components = [
            task_id,
            repeat_id,
            *args,
        ]

        path = os.path.join(self.save_dir, *[_ for _ in path_components if _])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

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
        path_file = self._get_save_path(
            task_id,
            repeat_id,
            self.SOLUTION_FILE_NAME,
        )
        with open(path_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=4)

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
        path_file = self._get_save_path(
            task_id,
            repeat_id,
            self.EVALUATION_DIR_NAME,
            f"{evaluation.name}.json",
        )
        with open(path_file, "w", encoding="utf-8") as f:
            json.dump(evaluation, f, ensure_ascii=False, indent=4)

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
        path_file = self._get_save_path(
            task_id,
            repeat_id,
            self.EVALUATION_DIR_NAME,
            f"{metric_name}.json",
        )
        if not os.path.exists(path_file):
            raise FileNotFoundError(path_file)
        with open(path_file, "r", encoding="utf-8") as f:
            evaluation = json.load(f)
        return MetricResult(**evaluation)

    def get_solution_result(
        self,
        task_id: str,
        repeat_id: str,
        **kwargs: Any,
    ) -> SolutionOutput:
        """Get the solution result for the given task and repeat id from the
        file system.

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task, usually the index of the repeat
                evaluation.

        Raises:
            `FileNotFoundError`:
                If the solution result file does not exist for the given task
                and repeat ID.

        Returns:
            `SolutionOutput`:
                The solution output for the given task and repeat ID.
        """
        path_file = self._get_save_path(
            task_id,
            repeat_id,
            self.SOLUTION_FILE_NAME,
        )
        if not os.path.exists(path_file):
            raise FileNotFoundError(
                f"Solution result for task {task_id} and repeat {repeat_id} "
                "not found.",
            )

        try:
            with open(path_file, "r", encoding="utf-8") as f:
                solution_data = json.load(f)
        except JSONDecodeError as e:
            raise JSONDecodeError(
                f"Failed to load JSON from {path_file}: {e.msg}",
                e.doc,
                e.pos,
            ) from e

        return SolutionOutput(**solution_data)

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
        path_file = self._get_save_path(
            task_id,
            repeat_id,
            self.SOLUTION_FILE_NAME,
        )

        return os.path.exists(path_file) and os.path.getsize(path_file) > 0

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
        path_file = self._get_save_path(
            task_id,
            repeat_id,
            self.EVALUATION_DIR_NAME,
            f"{metric_name}.json",
        )
        return os.path.exists(path_file) and os.path.getsize(path_file) > 0

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
        path_file = os.path.join(
            self.save_dir,
            self.EVALUATION_RESULT_FILE,
        )
        os.makedirs(os.path.dirname(path_file), exist_ok=True)
        with open(path_file, "w", encoding="utf-8") as f:
            json.dump(aggregation_result, f, ensure_ascii=False, indent=4)

    def aggregation_result_exists(
        self,
        **kwargs: Any,
    ) -> bool:
        """Check if the aggregation result exists

        Returns:
            `bool`:
                `True` if the aggregation result file exists.
        """
        path_file = os.path.join(
            self.save_dir,
            self.EVALUATION_RESULT_FILE,
        )
        return os.path.exists(path_file) and os.path.getsize(path_file) > 0

    def save_evaluation_meta(self, meta_info: dict) -> None:
        """Save the evaluation meta information.

        Args:
            meta_info (`dict`):
                A dictionary containing the meta information.
        """
        path_file = os.path.join(
            self.save_dir,
            self.EVALUATION_META_FILE,
        )
        os.makedirs(os.path.dirname(path_file), exist_ok=True)
        with open(path_file, "w", encoding="utf-8") as f:
            json.dump(meta_info, f, ensure_ascii=False, indent=4)

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
        path_file = self._get_save_path(
            task_id,
            None,
            self.TASK_META_FILE,
        )
        with open(path_file, "w", encoding="utf-8") as f:
            json.dump(meta_info, f, ensure_ascii=False, indent=4)

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
        path_file = self._get_save_path(
            task_id,
            repeat_id,
            self.SOLUTION_STATS_FILE_NAME,
        )
        if not os.path.exists(path_file):
            with open(path_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=4)

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
        path_file = self._get_save_path(
            task_id,
            repeat_id,
            self.SOLUTION_STATS_FILE_NAME,
        )

        if not os.path.exists(path_file):
            raise FileNotFoundError(
                f"Solution statistics for task {task_id} and repeat "
                f"{repeat_id} not found.",
            )

        try:
            with open(path_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except JSONDecodeError as e:
            raise JSONDecodeError(
                f"Failed to load JSON from {path_file}: {e.msg}",
                e.doc,
                e.pos,
            ) from e

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

        def pre_print_hook(_agent: AgentBase, kwargs: dict) -> None:
            """Hook function to save agent's printing."""
            msg: Msg | None = kwargs.get("msg", None)
            last: bool = kwargs.get("last", False)

            if msg is None or not last:
                return

            # Only save the last message
            printing_str = []
            for block in msg.get_content_blocks():
                match block["type"]:
                    case "text":
                        printing_str.append(
                            f"{msg.name}: {block['text']}",
                        )
                    case "thinking":
                        printing_str.append(
                            f"{msg.name} (thinking): {block['text']}",
                        )
                    case _:
                        block_str = json.dumps(
                            block,
                            ensure_ascii=False,
                            indent=4,
                        )
                        if printing_str:
                            printing_str.append(block_str)
                        else:
                            printing_str.append(f"{msg.name}: {block_str}")

            path_file = self._get_save_path(
                task_id,
                repeat_id,
                self.AGENT_PRINTING_LOG,
            )
            with open(path_file, "a", encoding="utf-8") as f:
                f.write("\n".join(printing_str) + "\n")

        return pre_print_hook
