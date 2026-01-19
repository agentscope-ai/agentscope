# -*- coding: utf-8 -*-
"""AlgorithmConfig definition for tuner."""

from pydantic import BaseModel, Field


class AlgorithmConfig(BaseModel):
    """Algorithm configuration for tuning."""

    algorithm_type: str = Field(
        description=(
            "The tuning algorithm type "
            "e.g., 'multi_step_grpo', 'sft'."
            "Please refer to https://github.com/agentscope-ai/Trinity-RFT"
            "for all supported algorithms. We recommend 'multi_step_grpo'"
            "for most agent tuning scenarios."
        ),
        default="multi_step_grpo",
    )
    learning_rate: float = Field(
        description="The learning rate for the algorithm.",
        default=1e-6,
    )
    group_size: int = Field(
        description=(
            "The group size for algorithms "
            "requiring group rollout, e.g., GRPO."
        ),
        default=8,
    )
    batch_size: int = Field(
        description="The batch size of each training step.",
        default=32,
    )
    save_interval_steps: int = Field(
        description="The interval steps to save the model.",
        default=100,
    )
    eval_interval_steps: int = Field(
        description="The interval steps to evaluate the model.",
        default=100,
    )
