# -*- coding: utf-8 -*-
"""The agent config classes."""

from pydantic import BaseModel, Field

from ..model import ChatModelBase


DEFAULT_SELF_COMPACT_RUBRIC_PROMPT = (
    # Inspired by SELFCOMPACT in https://arxiv.org/pdf/2606.23525:
    # ask the model whether the current trajectory is safe to summarize before
    # replacing old context with a continuation summary.
    "<system-hint>You are deciding whether the current agent trajectory "
    "should be compacted before the next reasoning step. Compress when the "
    "older conversation contains enough completed work, tool results, or "
    "intermediate reasoning that a concise continuation summary would preserve "
    "progress while reducing distraction or context cost. Continue when the "
    "recent details are still directly needed verbatim, when there is too "
    "little history to summarize, or when compaction would likely remove "
    "information needed for the next action. Return COMPRESS or CONTINUE."
    "</system-hint>"
)
"""Default rubric prompt for model-driven context compaction."""


SELF_COMPACT_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["COMPRESS", "CONTINUE"],
            "description": "Whether to compact the current context.",
        },
        "reason": {
            "type": "string",
            "description": "A concise reason for the decision.",
        },
    },
    "required": ["decision", "reason"],
    "additionalProperties": False,
}
"""Structured output schema for self-compaction decisions."""


class SummarySchema(BaseModel):
    """The compressed memory model, used to generate summary of old memories"""

    task_overview: str = Field(
        description=(
            "The user's core request and success criteria.\n"
            "Any clarifications or constraints they specified"
        ),
    )
    current_state: str = Field(
        description=(
            "What has been completed so far.\n"
            "File created, modified, or analyzed (with paths if relevant).\n"
            "Key outputs or artifacts produced."
        ),
    )
    important_discoveries: str = Field(
        description=(
            "Technical constraints or requirements uncovered.\n"
            "Decisions made and their rationale.\n"
            "Errors encountered and how they were resolved.\n"
            "What approaches were tried that didn't work (and why)"
        ),
    )
    next_steps: str = Field(
        description=(
            "Specific actions needed to complete the task.\n"
            "Any blockers or open questions to resolve.\n"
            "Priority order if multiple steps remain"
        ),
    )
    context_to_preserve: str = Field(
        description=(
            "User preferences or style requirements.\n"
            "Domain-specific details that aren't obvious.\n"
            "Any promises made to the user"
        ),
    )
    """Whether to execute multiple tool calls in parallel within one
    reasoning step."""


class ContextConfig(BaseModel):
    """The context related configuration in AgentScope"""

    model_config = {"arbitrary_types_allowed": True}
    """Allow arbitrary types in the pydantic model."""

    trigger_ratio: float = Field(default=0.8, gt=0, lt=0.9)
    """When the token exceeds this ratio of the maximum context length, the
    context will be compressed. To reserve the context for context compression,
    the maximum ratio is 0.9."""

    reserve_ratio: float = Field(default=0.1, gt=0, lt=0.9)
    """The ratio of the tokens to reserve in context compression, which should
    be smaller than the trigger ratio."""

    compression_prompt: str = Field(
        default=(
            "<system-hint>You have been working on the task described above "
            "but have not yet completed it. "
            "Now write a continuation summary that will allow you to resume "
            "work efficiently in a future context window where the "
            "conversation history will be replaced with this summary. "
            "Your summary should be structured, concise, and actionable."
            "</system-hint>"
        ),
        # ``format: textarea`` is a hint for schema-driven UI renderers
        # to use a multi-line input. Plain JSON Schema doesn't natively
        # express this, so we piggy-back on ``json_schema_extra``.
        json_schema_extra={"format": "textarea"},
    )
    """The prompt used to guide the compression model to generate the
    compressed summary, which will be wrapped into a user message and
    attach to the end of the current memory."""

    summary_template: str = Field(
        default=(
            "<system-info>Here is a summary of your previous work\n"
            "# Task Overview\n"
            "{task_overview}\n\n"
            "# Current State\n"
            "{current_state}\n\n"
            "# Important Discoveries\n"
            "{important_discoveries}\n\n"
            "# Next Steps\n"
            "{next_steps}\n\n"
            "# Context to Preserve\n"
            "{context_to_preserve}"
            "</system-info>"
        ),
        json_schema_extra={"format": "textarea"},
    )
    """The string template to present the compressed summary to the agent,
    which will be formatted with the fields from the
    `compression_summary_model`."""

    summary_schema: dict = Field(
        default_factory=SummarySchema.model_json_schema,
    )
    """The structured model used to guide the agent to generate the
    structured compressed summary."""

    tool_result_limit: int = Field(
        title="Tool Result Limit",
        default=50000,
        description=(
            "The maximum length of the tool results in tokens. "
            "If exceeded, the tool result will be truncated."
        ),
    )
    """The tool result limit to avoid tool result bursting."""

    self_compact_enabled: bool = Field(
        default=False,
        description=(
            "Whether to enable model-driven self-compaction. When disabled, "
            "AgentScope keeps the existing token-threshold compression "
            "behavior."
        ),
    )
    """Whether to enable model-driven self-compaction."""

    self_compact_probe_interval: int = Field(
        default=1,
        ge=1,
        description=(
            "Run the self-compaction rubric every N reasoning iterations when "
            "self-compaction is enabled."
        ),
    )
    """How often to run the self-compaction rubric."""

    self_compact_min_iters: int = Field(
        default=1,
        ge=0,
        description=(
            "Skip self-compaction rubric checks until the current reply has "
            "reached this reasoning iteration."
        ),
    )
    """Minimum current reply iteration before probing self-compaction."""

    self_compact_rubric_prompt: str = Field(
        default=DEFAULT_SELF_COMPACT_RUBRIC_PROMPT,
        json_schema_extra={"format": "textarea"},
    )
    """Prompt used to ask the model whether context should be compacted."""


class ReActConfig(BaseModel):
    """The reasoning related configuration"""

    max_iters: int = Field(
        title="Max Iterations",
        default=20,
        description="The maximum number of reasoning-acting iterations in "
        "one reply",
    )
    """The maximum number of iterations for the reasoning-acting loop."""

    stop_on_reject: bool = Field(
        title="Rejection Handling",
        default=False,
        description="Whether to stop replying when being rejected to "
        "execute tools.",
    )
    """If stop reasoning when tool call(s) are rejected. If `True`, the agent
    won't continue reasoning and wait for outside interaction from the user.
    """


class ModelConfig(BaseModel):
    """The model related configuration."""

    # TODO: remove this line after PR #1564 is merged, where the ChatModel
    #  will be child class of BaseModel
    model_config = {"arbitrary_types_allowed": True}

    max_retries: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of retries on top of the initial call before falling "
            "over to the fallback model. ``0`` means call the model exactly "
            "once and immediately move to the fallback on failure. Same "
            "semantics as ``ChatModelBase.max_retries``. Defaults to 0 to "
            "avoid compounding with the model's own inner retry loop."
        ),
    )
    """Number of retries on top of the initial call before falling over to
    the fallback model. ``0`` means a single attempt with no retries.
    Mirrors the semantics of ``ChatModelBase.max_retries``."""

    fallback_model: ChatModelBase | None = Field(
        default=None,
        description="The fallback model used when the main model fails.",
    )
    """The fallback model used when the main model fails. Also supports the
    max_retries logic."""
