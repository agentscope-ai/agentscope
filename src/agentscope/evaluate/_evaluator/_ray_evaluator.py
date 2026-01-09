# -*- coding: utf-8 -*-
"""The evaluator base class in agentscope."""
import asyncio
from typing import Callable, Awaitable, Coroutine, Any

from ._in_memory_exporter import _InMemoryExporter
from .._benchmark_base import BenchmarkBase
from .._evaluator._evaluator_base import EvaluatorBase
from .._solution import SolutionOutput
from .._task import Task
from .._evaluator_storage import EvaluatorStorageBase


def _check_ray_available() -> None:
    """Check if ray is available and raise ImportError if not."""
    try:
        import ray  # noqa  # pylint: disable=unused-import
    except ImportError as e:
        raise ImportError(
            "Ray is not installed. Please install it with `pip install ray` "
            "to use the RayEvaluator.",
        ) from e


# Create a conditional decorator for ray.remote
def _ray_remote_decorator(cls: Any) -> Any:
    """
    Conditional ray.remote decorator that only applies when ray is available.
    """
    try:
        import ray

        return ray.remote(cls)
    except ImportError:
        return cls


@_ray_remote_decorator
class RayEvaluationActor:
    """
    Actor class for running evaluation with ray remote.
    """

    @staticmethod
    async def run(
        storage: EvaluatorStorageBase,
        task: Task,
        repeat_id: str,
        solution_output: SolutionOutput,
    ) -> None:
        """
        Run the evaluation for a task and solution result.

        Args:
            storage (EvaluatorStorageBase): Evaluator storage.
            task (Task): Task to be evaluated.
            repeat_id (str): Repeat ID
            solution_output (SolutionOutput): output data after execute agents.
        """
        evaluation_results = await task.evaluate(solution_output)
        # store the evaluation result
        for result in evaluation_results:
            storage.save_evaluation_result(
                task_id=task.id,
                repeat_id=repeat_id,
                evaluation=result,
            )


@_ray_remote_decorator
class RaySolutionActor:
    """
    Actor class for running agent solutions with ray remote.
    """

    def __init__(self, n_workers: int = 1):
        self.eval_actor = RayEvaluationActor.options(
            max_concurrency=n_workers,
        ).remote()

        # Set up global exporter for this Actor
        self.exporter = _InMemoryExporter()

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        span_processor = SimpleSpanProcessor(self.exporter)
        tracer_provider: TracerProvider = trace.get_tracer_provider()
        if not isinstance(tracer_provider, TracerProvider):
            # Create a new tracer provider if not exists
            tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)

    async def run(
        self,
        storage: EvaluatorStorageBase,
        repeat_id: str,
        task: Task,
        solution: Callable[
            [Task, Callable],
            Coroutine[Any, Any, SolutionOutput],
        ],
    ) -> None:
        """Generate a solution to a task and evaluate.

        Args:
            storage (EvaluatorStorageBase): Evaluator storage.
            repeat_id (str): Repeat ID.
            task (Task): Task to be evaluated.
            solution
                (Callable[[Task, Callable], Awaitable[SolutionOutput, Any]]):
                callable function to execute agents and generate results.
        """
        if storage.solution_result_exists(task.id, repeat_id):
            # Obtain from storage
            solution_result = storage.get_solution_result(
                task.id,
                repeat_id,
            )

        else:
            from opentelemetry import trace, baggage
            from opentelemetry.context import attach, detach

            tracer = trace.get_tracer(__name__)

            # Set baggage items
            ctx = baggage.set_baggage("task_id", task.id)
            ctx = baggage.set_baggage("repeat_id", repeat_id, context=ctx)

            # Attach the context with baggage
            token = attach(ctx)

            try:
                with tracer.start_as_current_span(
                    name=f"Solution_{task.id}_{repeat_id}",
                ):
                    from ... import _config

                    _config.trace_enabled = True

                    # Run the solution
                    solution_result = await solution(
                        task,
                        storage.get_agent_pre_print_hook(
                            task.id,
                            repeat_id,
                        ),
                    )
            finally:
                detach(token)
                # Ensure all spans are flushed
                trace.get_tracer_provider().force_flush()

            storage.save_solution_stats(
                task.id,
                repeat_id,
                self.exporter.cnt.get(task.id, {}).get(repeat_id, {}),
            )

            storage.save_solution_result(
                task.id,
                repeat_id,
                solution_result,
            )

        # Evaluate the solution with the metrics
        futures = []
        for metric in task.metrics:
            if not storage.evaluation_result_exists(
                task.id,
                repeat_id,
                metric.name,
            ):
                futures.append(
                    self.eval_actor.run.remote(
                        storage,
                        task,
                        repeat_id,
                        solution_result,
                    ),
                )
        if futures:
            await asyncio.gather(*futures)


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

        # Check ray availability early
        _check_ray_available()

        assert isinstance(benchmark, BenchmarkBase)

        assert n_repeat >= 1, "n_repeat must be at least 1"

        assert n_workers >= 1, "n_workers must be at least 1"

        self.benchmark = benchmark
        self.n_repeat = n_repeat
        self.n_workers = n_workers

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

        # Create solution actors
        futures = []
        solution_actor = RaySolutionActor.options(
            max_concurrency=self.n_workers,
        ).remote(n_workers=self.n_workers)

        # Iterate over all tasks in the benchmark
        for task in self.benchmark:
            # Save the task meta information
            await self._save_task_meta(task)

            # Run n_repeat times
            for repeat_id in range(self.n_repeat):
                futures.append(
                    solution_actor.run.remote(
                        self.storage,
                        str(repeat_id),
                        task,
                        solution,
                    ),
                )

        # Await all the futures
        if futures:
            await asyncio.gather(*futures)

        # Aggregate the results
        await self.aggregate()
