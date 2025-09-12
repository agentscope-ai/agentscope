# -*- coding: utf-8 -*-
"""
.. _eval:

智能体评测
=========================

AgentScope 提供了一个内置的评测框架，用于评测智能体在不同任务和基准测试中的性能，主要特性包括：

- 基于 `Ray <https://github.com/ray-project/ray>`_ 的并行和分布式评估
- 支持中断后继续评估
- [开发中] 评估结果可视化

.. note:: 我们正在持续集成新的基准测试到 AgentScope 中：

 - ✅ `ACEBench <https://github.com/ACEBench/ACEBench>`_
 - 🚧 `GAIA <https://huggingface.co/datasets/gaia-benchmark/GAIA/tree/main>`_ 基准测试


概述
---------------------------

AgentScope 评估框架由几个关键组件组成：

- **基准测试 (Benchmark)**: 用于系统性评估的任务集合
    - **任务 (Task)**: 包含输入、标准答案和指标的独立评估单元
        - **指标 (Metric)**: 评估解决方案质量的测量函数
- **评估器 (Evaluator)**: 运行评估的引擎，聚合结果并分析性能
    - **评估器存储 (Evaluator Storage)**: 用于记录和检索评估结果的持久化存储
- **解决方案 (Solution)**: 用户定义的解决方案

.. figure:: ../../_static/images/evaluation.png
    :width: 90%
    :alt: AgentScope 评估框架

    *AgentScope 评估框架*

AgentScope 当前的实现包括：

- 评估器：
    - ``RayEvaluator``: 基于 ray 的评估器，支持并行和分布式评估。
    - ``GeneralEvaluator``: 通用评估器，按顺序运行任务，便于调试。
- 基准测试：
    - ``ACEBench``: 用于评估智能体能力的基准测试。

我们在 `GitHub 仓库 <https://github.com/agentscope-ai/agentscope/tree/main/examples/evaluation/ace_bench>`_ 中提供了一个使用 ``RayEvaluator`` 和 ACEBench 中智能体多步骤任务的玩具示例。

核心组件
---------------
我们将构建一个简单的玩学问题基准测试来演示如何使用 AgentScope 评估模块。
"""

TOY_BENCHMARK = [
    {
        "id": "math_problem_1",
        "question": "What is 2 + 2?",
        "ground_truth": 4.0,
        "tags": {
            "difficulty": "easy",
            "category": "math",
        },
    },
    {
        "id": "math_problem_2",
        "question": "What is 12345 + 54321 + 6789 + 9876?",
        "ground_truth": 83331,
        "tags": {
            "difficulty": "medium",
            "category": "math",
        },
    },
]

# %%
# 从任务、解决方案和指标到基准测试
# ~~~~~~~~~~~~~~~~~~~
#
# - 一个 ``SolutionOutput`` (Agent解决方案输出) 包含智能体生成的所有信息，包括轨迹和最终输出。
# - 一个 ``Metric`` (评测指标) 代表一个单一的评估可调用实例，它将生成的解决方案（例如，轨迹或最终输出）与标准答案进行比较。
# 在这个示例中，我们定义了一个指标，简单地检查解决方案中的 ``output`` 字段是否与标准答案匹配。

from agentscope.evaluate import (
    SolutionOutput,
    MetricBase,
    MetricResult,
    MetricType,
)


class CheckEqual(MetricBase):
    def __init__(
        self,
        ground_truth: float,
    ):
        super().__init__(
            name="math check number equal",
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


# %%
# - 一个 ``Task`` (任务) 是基准测试中的一个单元，包含智能体执行和评估所需的所有信息（例如，输入/查询及其标准答案）。
# - 一个 ``Benchmark`` (基准测试) 组织多个任务进行系统性评估。

from typing import Generator
from agentscope.evaluate import (
    Task,
    BenchmarkBase,
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
        """遍历基准测试。"""
        for task in self.dataset:
            yield task

    def __getitem__(self, index: int) -> Task:
        """根据索引获取任务。"""
        return self.dataset[index]

    def __len__(self) -> int:
        """获取基准测试的长度。"""
        return len(self.dataset)


# %%
# 评估器
# ~~~~~~~~~~
#
# 评估器 (Evaluators) 管理评估过程。它们可以自动遍历
# 基准测试中的任务，并将每个任务输入到解决方案生成函数中，
# 开发者需要在其中定义运行智能体和检索
# 执行结果和轨迹的逻辑。下面是一个
# 使用我们的玩具基准测试运行 ``GeneralEvaluator`` (通用评估器) 的示例。如果有一个大型
# 基准测试，开发者希望通过并行化更高效地进行评估，
# ``RayEvaluator`` (Ray评估器) 也可作为内置解决方案使用。


import os
import asyncio
from typing import Callable
from pydantic import BaseModel

from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.agent import ReActAgent

from agentscope.evaluate import (
    GeneralEvaluator,
    FileEvaluatorStorage,
)


class ToyBenchAnswerFormat(BaseModel):
    answer_as_number: float


async def toy_solution_generation(
    task: Task,
    pre_hook: Callable,
) -> SolutionOutput:
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday. "
        "Your target is to solve the given task with your tools. "
        "Try to solve the task as best as you can.",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen-max",
            stream=False,
        ),
        formatter=DashScopeChatFormatter(),
    )
    agent.register_instance_hook(
        "pre_print",
        "save_logging",
        pre_hook,
    )
    msg_input = Msg("user", task.input, role="user")
    res = await agent(
        msg_input,
        structured_model=ToyBenchAnswerFormat,
    )
    return SolutionOutput(
        success=True,
        output=res.metadata.get("answer_as_number", None),
        trajectory=[],
    )


async def main() -> None:
    evaluator = GeneralEvaluator(
        name="Toy benchmark evaluation",
        benchmark=ToyBenchmark(),
        # 重复多少次
        n_repeat=1,
        storage=FileEvaluatorStorage(
            save_dir="./results",
        ),
        # 使用多少个工作进程
        n_workers=1,
    )

    # 运行评估
    await evaluator.run(toy_solution_generation)


asyncio.run(main())
