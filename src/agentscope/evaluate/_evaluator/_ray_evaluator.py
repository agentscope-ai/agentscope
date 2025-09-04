# -*- coding: utf-8 -*-
"""The evaluator base class in agentscope."""
import asyncio
from typing import Callable, Awaitable, Coroutine, Any

try:
    import ray
except ImportError:
    ray = None

from .._benchmark_base import BenchmarkBase
from .._evaluator._evaluator_base import EvaluatorBase
from .._solution import SolutionOutput
from .._task import Task
from .._evaluator_storage import EvaluatorStorageBase


def _lazy_ray_remote(func: Callable) -> Callable:
    """Decorator to lazily initialize ray.remote."""
    try:
        import ray as lazy_ray

        return lazy_ray.remote(func)

    except ImportError:
        return func


class RayEvaluator(EvaluatorBase):
    """The ray-based evaluator that supports distributed and parallel
    evaluation."""

    def __init__(
        self,
        name: str,
        benchmark: BenchmarkBase,
        n_repeat: int,
        storage: EvaluatorStorageBase,
        n_workers: int,
    ) -> None:
        """Initialize the evaluator."""
        super().__init__(
            name=name,
            benchmark=benchmark,
            n_repeat=n_repeat,
            storage=storage,
        )

        assert isinstance(benchmark, BenchmarkBase)

        assert n_repeat >= 1, "n_repeat must be at least 1"

        assert n_workers >= 1, "n_workers must be at least 1"

        self.benchmark = benchmark
        self.n_repeat = n_repeat
        self.n_workers = n_workers

    @staticmethod
    @_lazy_ray_remote
    def run_evaluation(
        storage: EvaluatorStorageBase,
        task: Task,
        repeat_id: str,
        solution_output: SolutionOutput,
    ) -> None:
        """Run the evaluation for a task and solution result."""
        evaluation_results = task.evaluate(solution_output)
        # store the evaluation result
        for result in evaluation_results:
            storage.save_evaluation_result(
                task_id=task.id,
                repeat_id=repeat_id,
                evaluation=result,
            )

    @staticmethod
    @_lazy_ray_remote
    def run_solution(
        storage: EvaluatorStorageBase,
        repeat_id: str,
        task: Task,
        solution: Callable[
            [Task, Callable],
            Coroutine[Any, Any, SolutionOutput],
        ],
    ) -> None:
        """Generate a solution to a task and evaluate."""
        if storage.solution_result_exists(task.id, repeat_id):
            # Obtain from storage
            solution_result = storage.get_solution_result(
                task.id,
                repeat_id,
            )

        else:
            # Run the solution
            solution_result = asyncio.run(
                solution(
                    task,
                    storage.get_agent_pre_print_hook(
                        task.id,
                        repeat_id,
                    ),
                ),
            )
            storage.save_solution_result(
                task.id,
                repeat_id,
                solution_result,
            )

        # Evaluate the solution with the
        futures = []
        for metric in task.metrics:
            if not storage.evaluation_result_exists(
                task.id,
                repeat_id,
                metric.name,
            ):
                futures.append(
                    RayEvaluator.run_evaluation.remote(
                        storage,
                        task,
                        repeat_id,
                        solution_result,
                    ),
                )
        ray.get(futures)

    async def run(
        self,
        solution: Callable[
            [Task, Callable],
            Awaitable[SolutionOutput] | SolutionOutput,
        ],
    ) -> None:
        """Run the ray-based distributed and parallel evaluation, and get the
        results.

        Args:
            solution (`Callable[[Task], SolutionOutput]`):
                A sync or async function that takes a `Task` instance as input
                and returns a `SolutionOutput` instance.
        """

        await self._save_evaluation_meta()

        futures = []
        for repeat_id in range(self.n_repeat):
            for task in self.benchmark:
                futures.append(
                    RayEvaluator.run_solution.remote(
                        self.storage,
                        str(repeat_id),
                        task,
                        solution,
                    ),
                )
        ray.get(futures)

        await self.aggregate()
