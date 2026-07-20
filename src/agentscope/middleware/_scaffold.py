# -*- coding: utf-8 -*-
"""Scaffold controller and middleware for agent execution guidance."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Literal,
    TYPE_CHECKING,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ._base import MiddlewareBase
from ..event import ModelCallEndEvent, ReplyEndEvent, ReplyStartEvent
from ..message import Msg, SystemMsg, UserMsg
from ..model import ChatModelBase
from ..tool import ToolChoice

if TYPE_CHECKING:
    from ..agent import Agent
    from ..model import ChatResponse


_ORNITH_1_0_SECTIONS = (
    (
        "Orient",
        "Restate the objective, constraints, available context, and success "
        "criteria before choosing an action.",
    ),
    (
        "Reason",
        "Break the task into the smallest useful next steps, and keep "
        "uncertainty explicit when evidence is incomplete.",
    ),
    (
        "Navigate",
        "Select the next tool call, message, or final answer that best "
        "advances the objective with minimal unnecessary work.",
    ),
    (
        "Inspect",
        "After each observation or tool result, compare the new evidence "
        "against the plan and revise the route when needed.",
    ),
    (
        "Terminate",
        "Stop once the requested outcome is satisfied, and report the result "
        "clearly with any important verification or residual risk.",
    ),
    (
        "Handoff",
        "When delegating or asking for help, pass along the objective, "
        "current state, constraints, and the exact next decision needed.",
    ),
)

_DEFAULT_TEMPLATE = """<scaffold name="{name}" intensity="{intensity}">
Use the following execution scaffold as private operating guidance. Apply it
to structure your work, but do not expose these headings unless the user asks
for process details or the answer genuinely benefits from them.
{sections}
</scaffold>"""

_MODEL_CONTROLLER_PROMPT = """You are a scaffold controller for an AgentScope
ReAct agent. Given the task and available context, produce a compact scaffold
spec that improves the fixed ReAct pattern without changing the underlying
agent implementation.

Prefer conservative policies. Use tool_subset only when the task clearly
needs a narrow set of tools. Use step_budget to prevent wandering. Add verify
policy when the task has an observable success condition."""


class ScaffoldSection(BaseModel):
    """A single scaffold step appended to the agent system prompt."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(
        ...,
        min_length=1,
        title="Name",
        description="Short name of this scaffold step.",
    )
    instruction: str = Field(
        ...,
        min_length=1,
        title="Instruction",
        description="Instruction text for this scaffold step.",
    )

    @field_validator("name", "instruction")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        """Normalize whitespace-only values before validation."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped


class ScaffoldSpec(BaseModel):
    """Controller output describing how to scaffold a ReAct rollout."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(default="ornith-1.0")
    intensity: Literal["light", "standard", "strict"] = Field(
        default="standard",
    )
    sections: tuple[ScaffoldSection, ...] = Field(
        default_factory=lambda: tuple(
            ScaffoldSection(name=name, instruction=instruction)
            for name, instruction in _ORNITH_1_0_SECTIONS
        ),
    )
    tool_subset: tuple[str, ...] | None = Field(default=None)
    memory_policy: str | None = Field(default=None)
    retry_policy: str | None = Field(default=None)
    step_budget: int | None = Field(default=None, ge=1)
    verify_policy: str | None = Field(default=None)
    reward_policy: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "name",
        "memory_policy",
        "retry_policy",
        "verify_policy",
        "reward_policy",
    )
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        """Trim optional string fields."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("tool_subset")
    @classmethod
    def _normalize_tool_subset(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        """Trim and deduplicate tool names while preserving order."""
        if value is None:
            return None
        seen: set[str] = set()
        tools: list[str] = []
        for item in value:
            tool = item.strip()
            if tool and tool not in seen:
                seen.add(tool)
                tools.append(tool)
        return tuple(tools) or None


@dataclass(frozen=True)
class ScaffoldPolicy:
    """Compiled policy consumed by :class:`ScaffoldMiddleware`."""

    system_prompt: str = ""
    tool_subset: tuple[str, ...] | None = None
    memory_policy: str | None = None
    retry_policy: str | None = None
    step_budget: int | None = None
    verify_policy: str | None = None
    reward_policy: str | None = None


class ScaffoldControllerBase(ABC):
    """Base class for task-aware scaffold controllers."""

    @abstractmethod
    async def build_spec(
        self,
        agent: "Agent",
        task: str | None,
    ) -> ScaffoldSpec:
        """Build a scaffold spec for the current task."""


class StaticScaffoldController(ScaffoldControllerBase):
    """Controller that returns a fixed scaffold spec."""

    def __init__(
        self,
        spec: ScaffoldSpec | None = None,
    ) -> None:
        """Initialize the static scaffold controller."""
        self._spec = spec or ScaffoldSpec()

    async def build_spec(
        self,
        agent: "Agent",
        task: str | None,
    ) -> ScaffoldSpec:
        """Return the configured scaffold spec."""
        return self._spec


class ModelScaffoldController(ScaffoldControllerBase):
    """Use a stronger model to synthesize a scaffold spec per task.

    The model-produced spec is validated through :class:`ScaffoldSpec`. If
    generation or validation fails and ``fallback`` is provided, the fallback
    controller is used instead.
    """

    def __init__(
        self,
        model: ChatModelBase,
        fallback: ScaffoldControllerBase | None = None,
        controller_prompt: str = _MODEL_CONTROLLER_PROMPT,
    ) -> None:
        """Initialize the model scaffold controller."""
        self._model = model
        self._fallback = fallback or StaticScaffoldController()
        self._controller_prompt = controller_prompt

    async def build_spec(
        self,
        agent: "Agent",
        task: str | None,
    ) -> ScaffoldSpec:
        """Generate a scaffold spec with structured output."""
        context = _compact_recent_context(agent)
        messages = [
            SystemMsg("system", self._controller_prompt),
            UserMsg(
                "user",
                "Build a scaffold spec for this AgentScope agent.\n\n"
                f"Agent name: {agent.name}\n"
                f"Task: {task or '(no explicit new task)'}\n\n"
                f"Recent context:\n{context or '(empty)'}",
            ),
        ]
        try:
            response = await self._model.generate_structured_output(
                messages,
                ScaffoldSpec,
            )
            return ScaffoldSpec.model_validate(response.content)
        except Exception:
            return await self._fallback.build_spec(agent, task)


class ScaffoldCompiler:
    """Compile scaffold specs into prompt/tool/budget/verify policies."""

    def __init__(
        self,
        template: str = _DEFAULT_TEMPLATE,
    ) -> None:
        """Initialize the scaffold compiler."""
        self._template = template
        self._validate_template(template)

    def compile(self, spec: ScaffoldSpec) -> ScaffoldPolicy:
        """Compile a scaffold spec into runtime policy."""
        sections = "\n".join(
            f"- {section.name}: {section.instruction}"
            for section in spec.sections
        )
        prompt = self._template.format(
            name=spec.name,
            intensity=spec.intensity,
            sections=sections,
        ).strip()

        policy_lines = []
        if spec.memory_policy:
            policy_lines.append(f"Memory Policy: {spec.memory_policy}")
        if spec.retry_policy:
            policy_lines.append(f"Retry Policy: {spec.retry_policy}")
        if spec.step_budget:
            policy_lines.append(
                "Step Budget: Complete the task within "
                f"{spec.step_budget} reasoning/action iteration(s).",
            )
        if spec.verify_policy:
            policy_lines.append(f"Verify Policy: {spec.verify_policy}")
        if spec.reward_policy:
            policy_lines.append(f"Reward Policy: {spec.reward_policy}")

        if policy_lines:
            prompt = f"{prompt}\n<scaffold-policies>\n"
            prompt += "\n".join(f"- {line}" for line in policy_lines)
            prompt += "\n</scaffold-policies>"

        return ScaffoldPolicy(
            system_prompt=prompt,
            tool_subset=spec.tool_subset,
            memory_policy=spec.memory_policy,
            retry_policy=spec.retry_policy,
            step_budget=spec.step_budget,
            verify_policy=spec.verify_policy,
            reward_policy=spec.reward_policy,
        )

    @staticmethod
    def _validate_template(value: str) -> None:
        """Ensure the scaffold template can render the required fields."""
        for placeholder in ("{name}", "{intensity}", "{sections}"):
            if placeholder not in value:
                raise ValueError(
                    "template must contain the "
                    f"{placeholder!r} placeholder",
                )


class ScaffoldMiddleware(MiddlewareBase):
    """Apply ScaffoldController policies through existing Agent hooks.

    The middleware is intentionally low-intrusion: it uses the existing
    middleware hook points to inject prompt guidance, narrow model-visible
    tools, constrain per-reply step budget, and collect trace summary. The
    underlying Agent and ReAct loop remain unchanged.
    """

    class Parameters(BaseModel):
        """User-tunable scaffold parameters."""

        model_config = ConfigDict(frozen=True)

        enabled: bool = Field(
            default=True,
            title="Enabled",
            description="Whether to append scaffold guidance.",
        )
        name: Literal["ornith-1.0", "custom"] = Field(
            default="ornith-1.0",
            title="Scaffold",
            description="Built-in scaffold preset name, or custom.",
        )
        intensity: Literal["light", "standard", "strict"] = Field(
            default="standard",
            title="Intensity",
            description=(
                "How strongly the agent should follow the scaffold. The "
                "value is included in the prompt for model-side guidance."
            ),
        )
        sections: tuple[ScaffoldSection, ...] | None = Field(
            default=None,
            title="Sections",
            description=(
                "Optional custom scaffold sections. When omitted, the "
                "ornith-1.0 preset sections are used."
            ),
        )
        tool_subset: tuple[str, ...] | None = Field(default=None)
        memory_policy: str | None = Field(default=None)
        retry_policy: str | None = Field(default=None)
        step_budget: int | None = Field(default=None, ge=1)
        verify_policy: str | None = Field(default=None)
        reward_policy: str | None = Field(default=None)
        template: str = Field(
            default=_DEFAULT_TEMPLATE,
            title="Template",
            description=(
                "Prompt template containing {name}, {intensity}, and "
                "{sections} placeholders."
            ),
        )

        @field_validator("template")
        @classmethod
        def _validate_template(cls, value: str) -> str:
            """Ensure the scaffold template can render the required fields."""
            ScaffoldCompiler._validate_template(value)
            return value

    def __init__(
        self,
        parameters: "ScaffoldMiddleware.Parameters | None" = None,
        controller: ScaffoldControllerBase | None = None,
        compiler: ScaffoldCompiler | None = None,
    ) -> None:
        """Initialize the scaffold middleware.

        Args:
            parameters (`ScaffoldMiddleware.Parameters | None`, optional):
                Static scaffold configuration. Ignored when an explicit
                ``controller`` is provided, except for ``enabled`` and
                ``template``.
            controller (`ScaffoldControllerBase | None`, optional):
                Task-aware controller. Can be model-backed.
            compiler (`ScaffoldCompiler | None`, optional):
                Compiler that turns a spec into runtime policy.
        """
        self._parameters = parameters or ScaffoldMiddleware.Parameters()
        self._controller = controller or StaticScaffoldController(
            _spec_from_parameters(self._parameters),
        )
        self._compiler = compiler or ScaffoldCompiler(
            template=self._parameters.template,
        )

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Build per-reply scaffold policy and collect trace summary."""
        key = await self.get_middleware_key()
        task = _extract_task(input_kwargs.get("inputs"))
        policy = await self._build_policy(agent, task)
        _set_policy(agent, key, policy)
        trace = _new_trace(policy, task)

        try:
            async for event in next_handler(**input_kwargs):
                if isinstance(event, ReplyStartEvent):
                    trace["reply_id"] = event.reply_id
                elif isinstance(event, ModelCallEndEvent):
                    trace["model_calls"] += 1
                    trace["input_tokens"] += event.input_tokens
                    trace["output_tokens"] += event.output_tokens
                elif isinstance(event, ReplyEndEvent):
                    trace["finished_reason"] = str(event.finished_reason)
                yield event
        finally:
            if key not in agent.state.middle_context:
                agent.state.middle_context[key] = {}
            agent.state.middle_context[key]["last_trace"] = trace
            agent.state.middle_context[key].pop("policy", None)

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Apply tool whitelist and step-budget policy before reasoning."""
        key = await self.get_middleware_key()
        policy = _get_policy(agent, key)
        if policy is not None and policy.step_budget is not None:
            if agent.state.cur_iter >= policy.step_budget:
                input_kwargs["tool_choice"] = ToolChoice(mode="none")

        if policy is not None and policy.tool_subset:
            tool_choice = input_kwargs.get("tool_choice")
            input_kwargs["tool_choice"] = _merge_tool_choice(
                tool_choice,
                policy.tool_subset,
            )

        async for event in next_handler(**input_kwargs):
            yield event

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable["ChatResponse" | AsyncGenerator["ChatResponse", None]],
        ],
    ) -> "ChatResponse" | AsyncGenerator["ChatResponse", None]:
        """Filter model-visible tool schemas according to the policy."""
        key = await self.get_middleware_key()
        policy = _get_policy(agent, key)
        if policy is not None and policy.tool_subset:
            allowed = set(policy.tool_subset)
            input_kwargs["tools"] = [
                tool
                for tool in input_kwargs.get("tools", [])
                if _tool_schema_name(tool) in allowed
            ]

        return await next_handler(**input_kwargs)

    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append scaffold guidance to the system prompt when enabled."""
        if not self._parameters.enabled:
            return current_prompt

        key = await self.get_middleware_key()
        policy = _get_policy(agent, key)
        if policy is None:
            policy = await self._build_policy(agent, None)
            _set_policy(agent, key, policy)

        if not policy.system_prompt:
            return current_prompt
        return f"{current_prompt}\n\n{policy.system_prompt}"

    async def _build_policy(
        self,
        agent: "Agent",
        task: str | None,
    ) -> ScaffoldPolicy:
        """Build and compile the active scaffold policy."""
        if not self._parameters.enabled:
            return ScaffoldPolicy()
        spec = await self._controller.build_spec(agent, task)
        return self._compiler.compile(spec)


def _spec_from_parameters(
    parameters: ScaffoldMiddleware.Parameters,
) -> ScaffoldSpec:
    """Convert legacy/static middleware parameters to a controller spec."""
    return ScaffoldSpec(
        name=parameters.name,
        intensity=parameters.intensity,
        sections=parameters.sections
        or tuple(
            ScaffoldSection(name=name, instruction=instruction)
            for name, instruction in _ORNITH_1_0_SECTIONS
        ),
        tool_subset=parameters.tool_subset,
        memory_policy=parameters.memory_policy,
        retry_policy=parameters.retry_policy,
        step_budget=parameters.step_budget,
        verify_policy=parameters.verify_policy,
        reward_policy=parameters.reward_policy,
    )


def _extract_task(inputs: Any) -> str | None:
    """Extract a compact task string from reply inputs."""
    if isinstance(inputs, Msg):
        return inputs.get_text_content()
    if isinstance(inputs, list):
        parts = [
            msg.get_text_content()
            for msg in inputs
            if isinstance(msg, Msg) and msg.get_text_content()
        ]
        return "\n".join(parts) or None
    return None


def _compact_recent_context(agent: "Agent", limit: int = 6) -> str:
    """Return a short text snapshot of recent conversation context."""
    lines: list[str] = []
    for msg in agent.state.context[-limit:]:
        text = msg.get_text_content()
        if text:
            lines.append(f"{msg.role}:{msg.name}: {text}")
    return "\n".join(lines)


def _set_policy(
    agent: "Agent",
    key: str,
    policy: ScaffoldPolicy,
) -> None:
    """Store active scaffold policy in the agent middleware context."""
    if key not in agent.state.middle_context:
        agent.state.middle_context[key] = {}
    agent.state.middle_context[key]["policy"] = policy


def _get_policy(agent: "Agent", key: str) -> ScaffoldPolicy | None:
    """Read active scaffold policy from the agent middleware context."""
    data = agent.state.middle_context.get(key, {})
    policy = data.get("policy")
    return policy if isinstance(policy, ScaffoldPolicy) else None


def _merge_tool_choice(
    tool_choice: ToolChoice | str | None,
    tool_subset: tuple[str, ...],
) -> ToolChoice:
    """Merge scaffold tool subset into the current tool choice."""
    allowed = list(tool_subset)
    if tool_choice is None:
        return ToolChoice(mode="auto", tools=allowed)
    if isinstance(tool_choice, str):
        return ToolChoice(mode=tool_choice, tools=allowed)
    if tool_choice.mode in ["auto", "required", "none"]:
        tools = tool_choice.tools or allowed
        return ToolChoice(
            mode=tool_choice.mode,
            tools=[tool for tool in tools if tool in allowed],
        )
    return ToolChoice(mode=tool_choice.mode, tools=allowed)


def _tool_schema_name(tool: dict) -> str | None:
    """Extract a tool name from provider-neutral or OpenAI-style schemas."""
    if "name" in tool:
        return tool.get("name")
    function = tool.get("function")
    if isinstance(function, dict):
        return function.get("name")
    return None


def _new_trace(
    policy: ScaffoldPolicy,
    task: str | None,
) -> dict[str, Any]:
    """Create a trace record for reward/scaffold-store consumers."""
    return {
        "task": task,
        "reply_id": None,
        "tool_subset": list(policy.tool_subset or []),
        "step_budget": policy.step_budget,
        "verify_policy": policy.verify_policy,
        "reward_policy": policy.reward_policy,
        "model_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "finished_reason": None,
    }


__all__ = [
    "ModelScaffoldController",
    "ScaffoldCompiler",
    "ScaffoldControllerBase",
    "ScaffoldMiddleware",
    "ScaffoldPolicy",
    "ScaffoldSection",
    "ScaffoldSpec",
    "StaticScaffoldController",
]
