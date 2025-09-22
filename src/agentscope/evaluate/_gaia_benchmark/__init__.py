# -*- coding: utf-8 -*-
"""The GAIA benchmark related implementations in AgentScope."""

from ._gaia_benchmark import GAIABenchmark
from ._gaia_metric import (
    GAIAAccuracy,
)

__all__ = [
    "GAIABenchmark",
    "GAIAAccuracy",
]
