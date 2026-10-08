# -*- coding: utf-8 -*-
"""Context management settings for the realtime agent."""

from pydantic import BaseModel, Field, model_validator

from ...model import ChatModelBase
from .._config import SummarySchema


class RealtimeContextConfig(BaseModel):
    """Configure background summarization and session rollover.

    The compression model is separate from the realtime audio model. A
    context limit can override the realtime model card when necessary.

    Args:
        compression_model (`ChatModelBase`):
            Chat model used to summarize completed realtime turns.
        context_length (`int | None`, optional):
            Override for the realtime model card's token limit.
        trigger_ratio (`float`, optional):
            Usage ratio at which background compression starts.
        rollover_ratio (`float`, optional):
            Usage or session lifetime ratio requiring a new session.
        reserve_ratio (`float`, optional):
            Fraction of the local transcript retained verbatim.
        tool_result_limit (`int`, optional):
            Approximate token limit for a provider-visible tool result.
        max_audio_backlog_s (`float`, optional):
            Maximum buffered input audio duration during reconnect.
        compression_prompt (`str`, optional):
            Instructions sent to the summarization model.
        summary_schema (`dict`, optional):
            Structured output schema for the summary.
        summary_template (`str`, optional):
            Template for the context injected into a new session.
    """

    model_config = {"arbitrary_types_allowed": True}

    compression_model: ChatModelBase
    context_length: int | None = Field(default=None, gt=0)
    trigger_ratio: float = Field(default=0.7, gt=0, lt=1)
    rollover_ratio: float = Field(default=0.85, gt=0, lt=1)
    reserve_ratio: float = Field(default=0.2, ge=0, lt=1)
    tool_result_limit: int = Field(default=50000, gt=0)
    max_audio_backlog_s: float = Field(default=30.0, gt=0)
    compression_prompt: str = (
        "Summarize the earlier conversation for a new realtime session. "
        "Preserve the user's goals, important facts, decisions, promises, "
        "and any unfinished work. Keep the summary concise and "
        "self-contained."
    )
    summary_schema: dict = Field(
        default_factory=SummarySchema.model_json_schema,
    )
    summary_template: str = (
        "Task: {task_overview}\n"
        "Current state: {current_state}\n"
        "Important discoveries: {important_discoveries}\n"
        "Next steps: {next_steps}\n"
        "Context to preserve: {context_to_preserve}"
    )

    @model_validator(mode="after")
    def _validate_ratios(self) -> "RealtimeContextConfig":
        """Keep the retained tail below the trigger threshold."""
        if self.reserve_ratio >= self.trigger_ratio:
            raise ValueError("reserve_ratio must be below trigger_ratio")
        if self.rollover_ratio < self.trigger_ratio:
            raise ValueError("rollover_ratio must not be below trigger_ratio")
        return self
