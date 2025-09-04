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
)
from ._ace_benchmark import (
    ACEBenchmark,
    ACEAccuracy,
    ACEProcessAccuracy,
    ACEPhone,
)

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
    "Task",
    "SolutionOutput",
    "ACEBenchmark",
    "ACEAccuracy",
    "ACEProcessAccuracy",
    "ACEPhone",
]
