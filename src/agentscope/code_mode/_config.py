# -*- coding: utf-8 -*-
"""Bounds for one Python worker invocation."""

from pydantic import BaseModel, Field


class CodeModeConfig(BaseModel):
    """Execution limits; resource sampling is not an OS security boundary."""

    timeout: float = Field(default=120, gt=0, allow_inf_nan=False)
    tool_cleanup_timeout: float = Field(default=2, gt=0, allow_inf_nan=False)
    max_calls: int = Field(default=1000, gt=0)
    max_message_bytes: int = Field(default=16 * 1024 * 1024, ge=4096)
    max_output_bytes: int = Field(default=65536, ge=256)
    max_output_blocks: int = Field(default=256, ge=2)
    max_memory_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    max_cpu_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
