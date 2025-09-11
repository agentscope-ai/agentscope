# -*- coding: utf-8 -*-
"""Evaluation module tests in agentscope."""
import os
import shutil
from typing import Generator, Callable
from unittest.async_case import IsolatedAsyncioTestCase
import ray

from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.evaluate import (
    SolutionOutput,
    MetricBase,
    MetricResult,
    MetricType,
    Task,
    BenchmarkBase,
    GeneralEvaluator,
    RayEvaluator,
    FileEvaluatorStorage,
)


TASK_ID_1 = "math_problem_1"
TASK_ID_2 = "math_problem_2"

TOY_BENCHMARK = [
    {
        "id": TASK_ID_1,
        "question": "What is 2 + 2?",
        "ground_truth": 4.0,
        "tags": {
            "difficulty": "easy",
            "category": "math",
        },
    },
    {
        "id": TASK_ID_2,
        "question": "What is 12345 + 54321 + 6789 + 9876?",
        "ground_truth": 83331,
        "tags": {
            "difficulty": "medium",
            "category": "math",
        },
    },
]


METRIC_NAME = "math_check_number_equal"

class CheckEqual(MetricBase):
    def __init__(
        self,
        ground_truth: float,
    ):
        super().__init__(
            name=METRIC_NAME,
            metric_type=MetricType.NUMERICAL,
            description="Toy metric checking if two numbers are equal",
            categories=[],
        )
        self.ground_truth = ground_truth

    async def __call__(
        self,
        solution: SolutionOutput,
    ) -> MetricResult:
        if solution.output == self.ground_truth:
            return MetricResult(
                name=self.name,
                result=1.0,
                message="Correct",
            )
        else:
            return MetricResult(
                name=self.name,
                result=0.0,
                message="Incorrect",
            )


class ToyBenchmark(BenchmarkBase):
    def __init__(self):
        super().__init__(
            name="Toy bench",
            description="A toy benchmark for demonstrating the evaluation module.",
        )
        self.dataset = self._load_data()

    @staticmethod
    def _load_data() -> list[Task]:
        dataset = []
        for item in TOY_BENCHMARK:
            dataset.append(
                Task(
                    id=item["id"],
                    input=item["question"],
                    ground_truth=item["ground_truth"],
                    tags=item.get("tags", {}),
                    metrics=[
                        CheckEqual(item["ground_truth"]),
                    ],
                    metadata={},
                ),
            )
        return dataset

    def __iter__(self) -> Generator[Task, None, None]:
        """Iterate over the benchmark."""
        for task in self.dataset:
            yield task

    def __getitem__(self, index: int) -> Task:
        """Get a task by index."""
        return self.dataset[index]

    def __len__(self) -> int:
        """Get the length of the benchmark."""
        return len(self.dataset)


class TestEvalAgent(AgentBase):
    """Test agent class for testing hooks."""

    def __init__(self) -> None:
        """Initialize the test agent."""
        super().__init__()
        self.records: list[str] = []
        self.memory: list[Msg] = []

    async def reply(self, msg: Msg) -> Msg:
        """Reply to the message."""
        return Msg(
            name="test_eval_agent",
            content=[],
            role="assistant",
            metadata={"answer_as_number": 4.0}
        )

async def test_solution_generation(
    task: Task,
    pre_hook: Callable,
) -> SolutionOutput:
    agent = TestEvalAgent()

    msg_input = Msg("user", task.input, role="user")
    res = await agent(msg_input)
    return SolutionOutput(
        success=True,
        output=res.metadata.get("answer_as_number", None),
        trajectory=[],
    )

class EvaluatorTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.file_storage_general = FileEvaluatorStorage(
                save_dir="./general_results",
        )
        self.file_storage_ray = FileEvaluatorStorage(
            save_dir="./ray_results",
        )
        # Initialize Ray
        if not ray.is_initialized():
            ray.init()

    async def test_general_evaluator(self) -> None:
        evaluator = GeneralEvaluator(
            name="Test evaluation",
            benchmark=ToyBenchmark(),
            # Repeat how many times
            n_repeat=1,
            storage=self.file_storage_general,
            # How many workers to use
            n_workers=1,
        )

        # Run the evaluation
        await evaluator.run(test_solution_generation)
        metric_result_1 = self.file_storage_general.get_evaluation_result(
            task_id=TASK_ID_1,
            repeat_id="0",
            metric_name=METRIC_NAME
        )
        self.assertEqual(
            metric_result_1.result,
            1.0,
        )
        metric_result_2 = self.file_storage_general.get_evaluation_result(
            task_id=TASK_ID_2,
            repeat_id="0",
            metric_name=METRIC_NAME
        )
        self.assertEqual(
            metric_result_2.result,
            0.0,
        )

    async def test_ray_evaluator(self) -> None:
        evaluator = RayEvaluator(
            name="Test evaluation",
            benchmark=ToyBenchmark(),
            # Repeat how many times
            n_repeat=1,
            storage=self.file_storage_ray,
            # How many workers to use
            n_workers=1,
        )

        # Run the evaluation
        await evaluator.run(test_solution_generation)
        metric_result_1 = self.file_storage_ray.get_evaluation_result(
            task_id=TASK_ID_1,
            repeat_id="0",
            metric_name=METRIC_NAME
        )
        self.assertEqual(
            metric_result_1.result,
            1.0,
        )
        metric_result_2 = self.file_storage_ray.get_evaluation_result(
            task_id=TASK_ID_2,
            repeat_id="0",
            metric_name=METRIC_NAME
        )
        self.assertEqual(
            metric_result_2.result,
            0.0,
        )

    async def asyncTearDown(self) -> None:
        """Clean up the test environment."""
        # Shutdown Ray if it was initialized
        if ray.is_initialized():
            ray.shutdown()

        # clean written files
        if os.path.exists(self.file_storage_general.save_dir):
            shutil.rmtree(self.file_storage_general.save_dir)

        if os.path.exists(self.file_storage_ray.save_dir):
            shutil.rmtree(self.file_storage_ray.save_dir)
