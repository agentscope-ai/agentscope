# -*- coding: utf-8 -*-
"""The evaluation module in AgentScope."""

from ._evaluator import (
    EvaluatorBase,
    RayEvaluator,
    GeneralEvaluator,
)
from ._metric_base import (
    MetricBase,
    MetricResult,
    MetricType,
)
from ._task import Task
from ._solution import SolutionOutput
from ._benchmark_base import BenchmarkBase
from ._evaluator_storage import (
    EvaluatorStorageBase,
    FileEvaluatorStorage,
    AgentLoopEvaluatorStorage,
)
from ._ace_benchmark import (
    ACEBenchmark,
    ACEAccuracy,
    ACEProcessAccuracy,
    ACEPhone,
)
from ._agentloop_benchmark import (
    AgentLoopBenchmark,
)
from ._agentloop_config import AgentLoopConfig

__all__ = [
    "BenchmarkBase",
    "EvaluatorBase",
    "RayEvaluator",
    "GeneralEvaluator",
    "MetricBase",
    "MetricResult",
    "MetricType",
    "EvaluatorStorageBase",
    "FileEvaluatorStorage",
    "AgentLoopEvaluatorStorage",
    "Task",
    "SolutionOutput",
    "ACEBenchmark",
    "ACEAccuracy",
    "ACEProcessAccuracy",
    "ACEPhone",
    "AgentLoopBenchmark",
    "AgentLoopConfig",
]
