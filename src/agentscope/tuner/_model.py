# -*- coding: utf-8 -*-
"""TunerModelConfig definition."""
from __future__ import annotations
from typing import Dict, Any
from pydantic import BaseModel, Field


class TunerModelConfig(BaseModel):
    """Model configuration for tuning."""

    model_path: str = Field(
        description="The path to the model checkpoint.",
    )

    max_model_len: int = Field(
        description=(
            "The maximum length of the model, including context"
            " and generated tokens."
        ),
    )

    temperature: float = Field(
        description="Sampling temperature.",
        default=1.0,
    )

    top_p: float = Field(
        description="Top-p sampling parameter.",
        default=1.0,
    )

    max_tokens: int = Field(
        description="Maximum tokens for generation.",
        default=8192,
    )

    enable_thinking: bool | None = Field(
        description=(
            "Whether to enable thinking capability. "
            "Only applicable for Qwen3 series models."
        ),
        default=None,
    )

    tensor_parallel_size: int = Field(
        description="The tensor parallel size for model inference.",
        default=1,
    )

    inference_engine_num: int = Field(
        description="The number of engines for model inference.",
        default=1,
    )

    tool_call_parser: str = Field(
        description=(
            "The tool call parser to use. The default setting "
            "is for Qwen3 series models."
        ),
        default="hermes",
    )

    reasoning_parser: str = Field(
        description=(
            "The reasoning parser to use. The default "
            "setting is for Qwen3 series models."
        ),
        default="deepseek_r1",
    )

    tinker_config: TinkerConfig | None = Field(
        description=(
            "The configuration for Tinker. " "If None, Tinker is not used."
        ),
        default=None,
    )

    def get_config(self) -> Dict[str, Any]:
        """Get the model configuration.

        Returns:
            `Dict[str, Any]`: The model configuration dictionary.
        """
        return {
            "model_path": self.model_path,
            "max_model_len": self.max_model_len,
            "tensor_parallel_size": self.tensor_parallel_size,
            "engine_num": self.inference_engine_num,
            "tool_call_parser": self.tool_call_parser,
            "reasoning_parser": self.reasoning_parser,
            "enable_openai_api": True,
            "enable_auto_tool_choice": True,
        }


class TinkerConfig(BaseModel):
    """Model configuration for Tinker."""

    rank: int = Field(
        description="The LoRA rank of the Tinker model.",
        default=16,
    )

    seed: int | None = Field(
        description=(
            "The seed for initializing LoRA weights in the model. "
            "If None, weights are initialized randomly."
        ),
        default=None,
    )

    train_mlp: bool = Field(
        description="Whether to add LoRA to the MLP layers.",
        default=True,
    )

    train_attn: bool = Field(
        description="Whether to add LoRA to the attention layers.",
        default=True,
    )

    train_unembed: bool = Field(
        description="Whether to add LoRA to the unembedding layer.",
        default=True,
    )

    base_url: str | None = Field(
        description=(
            "The base URL for Tinker services. If None, the default "
            "service URL is used."
        ),
        default=None,
    )

    def get_config(self) -> Dict[str, Any]:
        """Get the Tinker model configuration.

        Returns:
            `Dict[str, Any]`: The Tinker model configuration dictionary.
        """
        return {
            "rank": self.rank,
            "seed": self.seed,
            "train_mlp": self.train_mlp,
            "train_attn": self.train_attn,
            "train_unembed": self.train_unembed,
            "base_url": self.base_url,
        }
