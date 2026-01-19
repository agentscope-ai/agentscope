"""Configuration module for prompt tuning."""

from typing import Any, Optional, Literal
from pydantic import BaseModel, Field


class PromptTuneConfig(BaseModel):
    """Configuration for prompt tuning.

    Attributes:
        lm_model_name: The model name for the prompt proposer.
        optimization_level: Optimization level, can be 'light', 'medium',
            or 'heavy'.
        eval_display_progress: Whether to display progress during evaluation.
        eval_display_table: Number of table rows to display during evaluation.
        eval_num_threads: Number of threads for evaluation.
        compare_performance: Whether to compare performance before and after
            tuning.
    """
    lm_model_name: str = Field(
        default="dashscope/qwen-plus",
        description="The model name for prompt proposer.",
    )

    optimization_level: Optional[Literal["light", "medium", "heavy"]] = Field(
        default="light",
        description="Optimization level, can be 'light', 'medium', or 'heavy'.",
    )

    eval_display_progress: bool = Field(
        default=True,
        description="Whether to display progress during evaluation",
    )
    eval_display_table: int = Field(
        default=5,
        description="Number of table rows to display during evaluation",
    )
    eval_num_threads: int = Field(
        default=16,
        description="Number of threads for evaluation",
    )

    compare_performance: bool = Field(
        default=True,
        description="Whether to compare performance before and after tuning",
    )
