# -*- coding: utf-8 -*-
"""The pipeline module."""

from ._base import PipelineProtocol
from ._goal_pipeline import GoalPipeline

__all__ = [
    "PipelineProtocol",
    "GoalPipeline",
]
