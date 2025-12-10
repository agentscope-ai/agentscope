# -*- coding: utf-8 -*-
"""General evaluator implementation in AgentScope, which is easy to debug
compared to the RayEvaluator."""
from typing import Callable, Awaitable, Coroutine, Any

from ._evaluator_base import EvaluatorBase
from ._in_memory_exporter import _InMemoryExporter
from .._evaluator_storage import EvaluatorStorageBase
from .._task import Task
from .._solution import SolutionOutput
from .._benchmark_base import BenchmarkBase


class GeneralEvaluator(EvaluatorBase):
    """The general evaluator that support users to debug their evaluation"""

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

    async def run_evaluation(
        self,
        task: Task,
        repeat_id: str,
        solution_output: SolutionOutput,
    ) -> None:
        """Run the evaluation for a task and solution result."""
        evaluation_results = await task.evaluate(solution_output)
        # store the evaluation result
        for result in evaluation_results:
            self.storage.save_evaluation_result(
                task_id=task.id,
                repeat_id=repeat_id,
                evaluation=result,
            )

    async def run_solution(
        self,
        repeat_id: str,
        task: Task,
        solution: Callable[[Task, Callable], Awaitable[SolutionOutput]],
    ) -> None:
        """Generate a solution to a task and evaluate."""
        if self.storage.solution_result_exists(task.id, repeat_id):
            # Obtain from storage
            solution_result = self.storage.get_solution_result(
                task.id,
                repeat_id,
            )

        else:
            from opentelemetry import trace
            from opentelemetry.context import attach, detach
            from opentelemetry import baggage

            tracer = trace.get_tracer(__name__)

            # Set baggage
            ctx = baggage.set_baggage("task_id", task.id)
            ctx = baggage.set_baggage("repeat_id", repeat_id, context=ctx)

            # Activate the context
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
                        self.storage.get_agent_pre_print_hook(
                            task.id,
                            repeat_id,
                        ),
                    )
                    self.storage.save_solution_result(
                        task.id,
                        repeat_id,
                        solution_result,
                    )
            finally:
                detach(token)

        # Evaluate the solution with the
        for metric in task.metrics:
            if not self.storage.evaluation_result_exists(
                task.id,
                repeat_id,
                metric.name,
            ):
                await self.run_evaluation(
                    task,
                    repeat_id,
                    solution_result,
                )

    async def run(
        self,
        solution: Callable[
            [Task, Callable],
            Coroutine[Any, Any, SolutionOutput],
        ],
    ) -> None:
        """Run the ray-based distributed and parallel evaluation, and get the
        results.

        Args:
            solution (`Callable[[Task, Callable], Coroutine[Any, Any, \
            SolutionOutput]]`):
                A async function that takes a `Task` instance and a pre-print
                hook function as input, returns a `SolutionOutput` instance.
        """

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        exporter = _InMemoryExporter()
        span_processor = SimpleSpanProcessor(exporter)

        tracer_provider: TracerProvider = trace.get_tracer_provider()
        if not isinstance(tracer_provider, TracerProvider):
            # Create a new tracer provider if not exists
            tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)

        await self._save_evaluation_meta()

        for task in self.benchmark:
            await self._save_task_meta(task)

            for repeat_id in range(self.n_repeat):
                await self.run_solution(
                    str(repeat_id),
                    task,
                    solution,
                )

                # Save the exporter data
                if (
                    task.id in exporter.cnt
                    and str(repeat_id) in exporter.cnt[task.id]
                ):
                    self.storage.save_solution_stats(
                        task.id,
                        str(repeat_id),
                        exporter.cnt[task.id][str(repeat_id)],
                    )

        await self.aggregate()
