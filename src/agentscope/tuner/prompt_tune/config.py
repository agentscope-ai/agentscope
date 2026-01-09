from typing import Any, Optional, Literal
from pydantic import BaseModel, Field


class PromptTuneConfig(BaseModel):
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
