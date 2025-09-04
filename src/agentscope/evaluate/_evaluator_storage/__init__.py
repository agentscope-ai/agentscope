# -*- coding: utf-8 -*-
"""The evaluator storage module in AgentScope."""

from ._evaluator_storage_base import EvaluatorStorageBase
from ._file_evaluator_storage import FileEvaluatorStorage

__all__ = [
    "EvaluatorStorageBase",
    "FileEvaluatorStorage",
]
