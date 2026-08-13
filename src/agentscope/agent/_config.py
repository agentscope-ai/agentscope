# -*- coding: utf-8 -*-
"""The agent config classes."""

from pydantic import BaseModel, Field, field_validator

from ..model import ChatModelBase


class SummarySchema(BaseModel):
    """The compressed memory model, used to generate summary of old memories"""

    task_overview: str = Field(
        description=(
            "用户的核心诉求与成功标准。\n"
            "用户提出的任何澄清或约束"
        ),
    )
    current_state: str = Field(
        description=(
            "目前已完成的内容。\n"
            "已创建、修改或分析的文件（如相关，附上路径）。\n"
            "产生的关键输出或产物。"
        ),
    )
    important_discoveries: str = Field(
        description=(
            "发现的技术约束或需求。\n"
            "已做出的决策及其理由。\n"
            "遇到的错误及解决方法。\n"
            "尝试过但没有奏效的方案（以及原因）"
        ),
    )
    next_steps: str = Field(
        description=(
            "完成任务所需的具体行动。\n"
            "需要解决的任何阻碍或待定问题。\n"
            "若仍有多个步骤，给出优先级顺序"
        ),
    )
    context_to_preserve: str = Field(
        description=(
            "用户的偏好或风格要求。\n"
            "不明显但具有领域特异性的细节。\n"
            "对用户做出的任何承诺"
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
            "<system-hint>你一直在处理上面描述的任务，"
            "但尚未完成它。"
            "现在请编写一份续接摘要，以便你在未来的上下文窗口"
            "（届时对话历史将被这份摘要取代）中能够高效地恢复工作。"
            "你的摘要应当结构清晰、简洁且具有可操作性。\n"
            "当前时间是 {current_time}。\n"
            "这份摘要以后可能还会被再次概括，而它所引用的对话历史"
            "将不复存在，因此每一处引用都必须自包含——把所有依赖"
            "已消失上下文的内容都解析为绝对、完整限定的形式：\n"
            "- 时间：使用上面的当前时间，将相对表达（'today'、'now'、"
            "'yesterday'、'tomorrow'、'recently'）转换为绝对日期；"
            "即使更早的摘要已经用相对方式写过，也要重新锚定。\n"
            "- 名称与指针：使用文件路径、符号名、PR/issue 编号、"
            "ID、URL，以及逐字保留的精确命令/错误字符串，"
            "而不是'this file'、'the above'、'the second approach'、"
            "'the 5 failing tests'。\n"
            "- 进行中的工作：记录所有仍待处理的事项，尤其是"
            "在后台启动、其运行结果你仍在等待的工具——"
            "为每一项给出其 id 和一句简短说明（它在做什么），"
            "并标注每项的归属（用户请求还是你自己的决定）"
            "和状态（done / pending / blocked）。\n"
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
            "<system-info>以下是你之前工作的摘要\n"
            "# 任务概述\n"
            "{task_overview}\n\n"
            "# 当前状态\n"
            "{current_state}\n\n"
            "# 重要发现\n"
            "{important_discoveries}\n\n"
            "# 下一步计划\n"
            "{next_steps}\n\n"
            "# 需要保留的上下文\n"
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


class InjectionConfig(BaseModel):
    """The state injection related configuration in AgentScope."""

    inject_runtime_state: bool = Field(
        title="Inject Runtime State",
        description=(
            "Inject the runtime state to context, including current time,"
            "tasks state, context length, etc."
        ),
        default=True,
    )
    """Whether to inject the runtime state to context, including current time,
    tasks state, context length, etc."""

    timezone: str = Field(
        title="Timezone",
        default="UTC",
        description=(
            "The injected timezone. e.g. 'America/New_York' or "
            "'Asia/Shanghai'."
        ),
    )
    """The timezone to inject into the context, follow the standard timezone
    database format, e.g. 'America/New_York' or 'Asia/Shanghai'."""

    time_format: str = Field(
        title="Time Format",
        default="%Y-%m-%dT%H:%M:%S",
        description=(
            "The format to inject and parse the time information, which must "
            "round-trip a full timestamp, i.e. carry the date part. A "
            "time-only format such as '%H:%M:%S' makes the parsed time fall "
            "back to year 1900, so that the time is injected in every "
            "iteration."
        ),
    )
    """The format to inject and parse the time information, which must carry
    the date part to round-trip a full timestamp."""

    time_interval: float = Field(
        title="Time Interval",
        default=0.5,
        ge=0,
        description=(
            "The minimum time interval in hours from the last injection to "
            "trigger new time injection"
        ),
    )
    """The minimum elapsed time in **hours** from the recorded time to trigger
    a new time injection."""

    context_buffer_ratio: float = Field(
        title="Context Buffer",
        default=0.2,
        ge=0,
        le=1,
        description=(
            "The buffer that will activate context length injection before "
            "context compression, which should be smaller than the "
            "'trigger_ratio' of the context config."
        ),
    )
    """The buffer ahead of the compression threshold, e.g. with a trigger ratio
    of 0.8 and a buffer of 0.2, the context length is injected once the input
    tokens exceed 60% of the model context size."""

    template: str = Field(
        title="Template",
        default="""<system-reminder>请在对话的这一时刻，将以下内容视为 \
事实依据。之前所述的一切均已过时，若之后还有提醒，则以更晚的 \
提醒为准：
{runtime_state}
</system-reminder>""",
        description=(
            "The template to wrap the injected runtime state, where the "
            "'{runtime_state}' placeholder will be replaced by the injected "
            "fields."
        ),
    )
    """The template to wrap the injected runtime state, which must contain the
    ``{runtime_state}`` placeholder."""

    @field_validator("template")
    @classmethod
    def _check_template(cls, value: str) -> str:
        """Ensure the template won't silently drop the injected fields."""
        if "{runtime_state}" not in value:
            raise ValueError(
                "The injection template must contain the '{runtime_state}' "
                f"placeholder, got {value!r}.",
            )
        return value

    injection_source: str = Field(
        title="Injection Source",
        default='{"label": "System", "sublabel": "Runtime State"}',
        description=(
            "The source of the injected hint block, which is also used to "
            "identify the previous injections within the context."
        ),
    )
    """The source of the injected hint block, used to identify the agent's own
    injections when scanning the context."""

    task_tool_names: list[str] = Field(
        title="Task Tool Names",
        default_factory=lambda: [
            "TaskCreate",
            "TaskGet",
            "TaskList",
            "TaskUpdate",
        ],
        description=(
            "The names of the task related tools. Their presence in the "
            "context suppresses the tasks injection."
        ),
    )
    """The names of the task related tools, whose tool calls in the context
    indicate the agent is already aware of the tasks."""

    extra_fields: dict[str, str] = Field(
        title="Extra Fields",
        default_factory=dict,
        description=(
            "The extra fields to inject, which will be wrapped into the "
            "'<{key}>{value}</{key}>' format."
        ),
    )
    """The user defined fields to inject, which are attached to the injection
    without triggering one by themselves."""

    emit_hint_event: bool = Field(
        title="Emit Hint Event",
        default=True,
        description=(
            "If emit the HintBlockEvent when runtime state injection happens."
        ),
    )


class ReActConfig(BaseModel):
    """The reasoning related configuration"""

    max_iters: int = Field(
        title="Max Iterations",
        default=20,
        description="The maximum number of reasoning-acting iterations in "
        "one reply",
    )
    """The maximum number of iterations for the reasoning-acting loop."""

    structured_output_grace_iters: int = Field(
        title="Grace Iters for Structured Output",
        description=(
            "The grace iterations for structured output when exceeding the "
            "max iterations"
        ),
        default=5,
        gt=0,
    )
    """The extra iterations allowed beyond ``max_iters`` to generate the
    required structured output."""

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
        default="我注意到你打断了。有什么可以帮你的吗？",
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
