# -*- coding: utf-8 -*-
"""The agent config classes."""

from pydantic import BaseModel, Field

from ..model import ChatModelBase


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
    """The important context to preserve across compression, e.g. user
    preferences, domain-specific details and promises made to the user."""


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
    `summary_schema`."""

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

    interruption_message: str = Field(
        title="Interruption Message",
        default="I notice the interruption. How can I help you?",
        description="The quick reply message when interrupted.",
    )
    """The interruption message."""

    interruption_raise_cancelled_error: bool = Field(
        title="Raise CancelledError on Interruption",
        default=False,
        description="Whether to re-raise ``asyncio.CancelledError`` after "
        "handling the interruption. When ``False``, the ``CancelledError`` "
        "is swallowed once the interruption context has been produced.",
    )
    """Whether to re-raise the ``asyncio.CancelledError`` after the
    interruption has been handled. When ``False``, the ``CancelledError``
    is swallowed once the fallback interruption message and
    ``ReplyEndEvent`` have been emitted."""


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
